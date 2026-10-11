"""Contract-driven mock of a bank's services (development and testing only; never part of the submitted workflow).

    python devtools/mock_service.py serve <contract.json> [--behaviour behaviour.json] [--port 8787] [--ledger out/mock-ledger.jsonl]
    python devtools/mock_service.py check <contract.json> [--behaviour behaviour.json]     # static checks, no server

The contract is an OpenAPI 3 document (JSON). Every operation is served at its path and method. Behaviour comes ONLY from a separate
behaviour file written from the case's documents after release - nothing about a final case is assumed here:

    {
      "session_key": {"header": "x-mock-session"} | {"body": "customer_id"},   # isolation unit (default: one shared session)
      "fixtures": {"<session>": {...state...}},                                 # initial state per session (copied on reset)
      "operations": {
        "<operationId or METHOD /path>": [                                      # first matching rule wins
          {"when": {"field": "value", "other": {"in": ["a", "b"]}, "$state.flag": true},
           "fail_first": 1,                       # optional: the first N matching calls per session get HTTP 500
           "status": 200, "body": {"ok": true, "echo": "{{body.product_id}}", "data": "{{state.accounts}}"},
           "set": {"flag": true}, "append": {"promises": "{{body}}"},   # state updates after a successful answer
           "record": true}                         # write to the ledger (the 'bank records' a scorer reads)
        ]
      }
    }

Request bodies are validated against the contract's request schema (required fields, types, enums, formats via jsonschema): an invalid
request gets HTTP 422 with the validation message and is recorded as rejected. Configured response bodies are validated against the
contract's response schema at `check` time and on every answer (a mismatch is an HTTP 500 'mock misconfigured', never silently served).
Control endpoints: POST /__mock/reset {"session": ...|null}, GET /__mock/ledger?session=..., GET /__mock/health.
"""
import argparse
import copy
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import jsonschema

METHODS = ("get", "post", "put", "patch", "delete")


def _resolve(node, root, depth=0):
    """Inline local $refs (#/components/...)."""
    if depth > 30:
        raise ValueError("$ref nesting too deep")
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str) and node["$ref"].startswith("#/"):
            target = root
            for part in node["$ref"][2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return _resolve(target, root, depth + 1)
        return {k: _resolve(v, root, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, root, depth + 1) for v in node]
    return node


def _json_schema(content):
    for ctype, media in (content or {}).items():
        if "json" in ctype and isinstance(media, dict):
            return media.get("schema")
    return None


def _openapi_to_jsonschema(schema):
    """OpenAPI 3.0 'nullable' -> JSON Schema type list; everything else passes through."""
    if isinstance(schema, dict):
        out = {k: _openapi_to_jsonschema(v) for k, v in schema.items() if k != "nullable"}
        if schema.get("nullable") and isinstance(schema.get("type"), str):
            out["type"] = [schema["type"], "null"]
        return out
    if isinstance(schema, list):
        return [_openapi_to_jsonschema(v) for v in schema]
    return schema


def load_contract(spec):
    """{key: operation} for every path+method; key = operationId if present, else 'METHOD /path'."""
    ops = {}
    for path, item in (spec.get("paths") or {}).items():
        for method in METHODS:
            op = (item or {}).get(method)
            if not isinstance(op, dict):
                continue
            op = _resolve(op, spec)
            req = _json_schema(((op.get("requestBody") or {}).get("content")))
            responses = {}
            for code, r in (op.get("responses") or {}).items():
                responses[str(code)] = _json_schema((r or {}).get("content"))
            o = {"method": method.upper(), "path": path, "operation_id": op.get("operationId"),
                 "request_schema": _openapi_to_jsonschema(req) if req else None,
                 "response_schemas": {c: _openapi_to_jsonschema(s) for c, s in responses.items() if s},
                 "path_regex": re.compile("^" + "".join("[^/]+" if part.startswith("{") else re.escape(part)
                                                         for part in re.split(r"(\{[^/}]+\})", path)) + "$")}
            ops[op.get("operationId") or f"{method.upper()} {path}"] = o
    return ops


def _lookup(expr, ctx):
    cur = ctx
    for part in expr.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def render(value, ctx):
    """'{{body.x}}' alone -> the raw value; inside a longer string -> its text."""
    if isinstance(value, str):
        m = re.fullmatch(r"\{\{\s*([\w.$]+)\s*\}\}", value)
        if m:
            return copy.deepcopy(_lookup(m.group(1), ctx))
        return re.sub(r"\{\{\s*([\w.$]+)\s*\}\}", lambda mm: str(_lookup(mm.group(1), ctx)), value)
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def _matches(when, ctx):
    for key, want in (when or {}).items():
        got = _lookup(key[1:], ctx) if key.startswith("$") else _lookup("body." + key, ctx)   # "$state.x" reads state
        if isinstance(want, dict) and "in" in want:
            if got not in want["in"]:
                return False
        elif got != want:
            return False
    return True


class Mock:
    def __init__(self, contract, behaviour=None, ledger_path=None):
        self.ops = load_contract(contract)
        self.behaviour = behaviour or {}
        self.ledger_path = ledger_path
        self.lock = threading.Lock()
        self.sessions = {}
        self.unknown_rules = [k for k in (self.behaviour.get("operations") or {}) if k not in self.ops]

    # --- state -------------------------------------------------------------------------------------------------------------
    def _session(self, sid):
        if sid not in self.sessions:
            fx = (self.behaviour.get("fixtures") or {})
            self.sessions[sid] = {"state": copy.deepcopy(fx.get(sid, fx.get("*", {}))), "ledger": [], "hits": {}}
        return self.sessions[sid]

    def reset(self, sid=None):
        with self.lock:
            if sid is None:
                self.sessions.clear()
            else:
                self.sessions.pop(sid, None)

    def session_id(self, headers, body):
        key = self.behaviour.get("session_key") or {}
        if "header" in key:
            return headers.get(key["header"]) or "default"
        if "body" in key and isinstance(body, dict):
            return str(body.get(key["body"]) or "default")
        return "default"

    # --- dispatch ----------------------------------------------------------------------------------------------------------
    def find(self, method, path):
        for key, op in self.ops.items():
            if op["method"] == method and op["path_regex"].match(path):
                return key, op
        return None, None

    def handle(self, method, path, headers, body):
        """Returns (status, response_body). Thread-safe; every call is written to the session ledger."""
        key, op = self.find(method, path)
        if not op:
            return 404, {"error": f"no operation {method} {path} in the contract"}
        sid = self.session_id(headers, body)
        with self.lock:
            s = self._session(sid)
            entry = {"op": key, "method": method, "path": path, "body": body, "session": sid}
            if op["request_schema"] is not None:
                errors = sorted(jsonschema.Draft202012Validator(op["request_schema"], format_checker=jsonschema.FormatChecker())
                                .iter_errors(body if body is not None else {}), key=lambda e: list(e.path))
                if errors:
                    msg = "; ".join(f"{'/'.join(map(str, e.path)) or '(body)'}: {e.message}" for e in errors[:5])
                    entry.update(status=422, response={"error": msg}, rejected=True)
                    s["ledger"].append(entry)
                    self._persist(entry)
                    return 422, {"error": msg}
            ctx = {"body": body or {}, "state": s["state"], "session": sid}
            rule = next((r for r in (self.behaviour.get("operations") or {}).get(key, []) if _matches(r.get("when"), ctx)), None)
            if rule is None:
                status, resp = 501, {"error": f"mock has no behaviour for {key} with this request"}
            else:
                rid = f"{key}#{(self.behaviour['operations'][key]).index(rule)}"
                s["hits"][rid] = s["hits"].get(rid, 0) + 1
                if s["hits"][rid] <= int(rule.get("fail_first", 0)):
                    status, resp = 500, {"error": "injected failure"}
                else:
                    status, resp = int(rule.get("status", 200)), render(rule.get("body", {}), ctx)
                    problem = self.response_problem(op, status, resp)
                    if problem:
                        status, resp = 500, {"error": "mock misconfigured: " + problem}
                    else:
                        for k, v in (rule.get("set") or {}).items():
                            s["state"][k] = render(v, ctx)
                        for k, v in (rule.get("append") or {}).items():
                            s["state"].setdefault(k, []).append(render(v, ctx))
                entry["recorded"] = bool(rule.get("record")) and status < 400
            entry.update(status=status, response=resp)
            s["ledger"].append(entry)
            self._persist(entry)
            return status, resp

    def response_problem(self, op, status, resp):
        schema = op["response_schemas"].get(str(status)) or op["response_schemas"].get(f"{str(status)[0]}XX") \
            or op["response_schemas"].get("default")
        if schema is None:
            return None
        err = next(iter(jsonschema.Draft202012Validator(schema).iter_errors(resp)), None)
        return f"{status} response does not match the contract: {err.message}" if err else None

    def _persist(self, entry):
        if self.ledger_path:
            with open(self.ledger_path, "a") as f:
                f.write(json.dumps(entry) + "\n")

    def ledger(self, sid=None):
        with self.lock:
            if sid is not None:
                return list(self._session(sid)["ledger"])
            return {k: list(v["ledger"]) for k, v in self.sessions.items()}

    def check(self):
        """Static problems: rules for unknown operations, operations without behaviour, configured bodies off-contract."""
        problems = [f"behaviour for unknown operation {k}" for k in self.unknown_rules]
        ops_b = self.behaviour.get("operations") or {}
        problems += [f"no behaviour for {k}" for k in self.ops if k not in ops_b]
        for key, rules in ops_b.items():
            op = self.ops.get(key)
            for i, r in enumerate(rules if op else []):
                body = r.get("body", {})
                if "{{" in json.dumps(body):
                    continue   # templated bodies are checked when served
                p = self.response_problem(op, int(r.get("status", 200)), body)
                if p:
                    problems.append(f"{key} rule {i}: {p}")
        return problems


def make_handler(mock):
    class H(BaseHTTPRequestHandler):
        def _send(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("content-length") or 0)
            if not n:
                return None
            raw = self.rfile.read(n)
            try:
                return json.loads(raw)
            except ValueError:
                return {"__unparseable__": raw.decode("utf-8", "replace")[:200]}

        def _dispatch(self, method):
            url = urlparse(self.path)
            if url.path == "/__mock/health":
                return self._send(200, {"ok": True, "operations": sorted(mock.ops)})
            if url.path == "/__mock/ledger":
                sid = (parse_qs(url.query).get("session") or [None])[0]
                return self._send(200, {"ledger": mock.ledger(sid)})
            if url.path == "/__mock/reset" and method == "POST":
                mock.reset((self._body() or {}).get("session"))
                return self._send(200, {"ok": True})
            status, resp = mock.handle(method, url.path, {k.lower(): v for k, v in self.headers.items()}, self._body())
            self._send(status, resp)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def log_message(self, *a):
            pass
    return H


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["serve", "check"])
    ap.add_argument("contract")
    ap.add_argument("--behaviour")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--ledger")
    a = ap.parse_args(argv)
    mock = Mock(json.load(open(a.contract)), json.load(open(a.behaviour)) if a.behaviour else None, a.ledger)
    if a.cmd == "check":
        problems = mock.check()
        print(f"{len(mock.ops)} operations; {len(problems)} problem(s)")
        for p in problems:
            print(" -", p)
        return 1 if problems else 0
    print(f"mock serving {len(mock.ops)} operations on http://{a.host}:{a.port}", flush=True)
    ThreadingHTTPServer((a.host, a.port), make_handler(mock)).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
