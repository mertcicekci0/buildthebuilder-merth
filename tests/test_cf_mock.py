"""devtools/cf_mock: the generated Cloudflare Worker behaves like devtools/mock_service.py on the same contract + behaviour.

Three implementations run the same scenarios: mock_service.Mock (the Python reference), the JS core with an in-memory store, and the
GENERATED worker.js (its fetch handler routing to the MockState Durable Object class over a fake DO storage), all under node.
The kit tool API is only a stand-in contract; the behaviour file is a test fixture, nothing from a final case."""
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "devtools"))
sys.path.insert(0, str(ROOT / "devtools" / "cf_mock"))
import build_worker  # noqa: E402
import mock_service as ms  # noqa: E402

NODE = shutil.which("node") or "/usr/local/bin/node"
pytestmark = pytest.mark.skipif(not pathlib.Path(NODE).exists(), reason="node is not installed")
HARNESS = ROOT / "devtools/cf_mock/harness.mjs"
CORE = ROOT / "devtools/cf_mock/core.js"
CONTRACT = json.loads((ROOT / "kit/tools/openapi.json").read_text())
BEHAVIOUR = json.loads((ROOT / "tests/fixtures/cf_mock/behaviour.json").read_text())


def _node(*args):
    r = subprocess.run([NODE, str(HARNESS), *map(str, args)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class Run:
    """Runs scenarios on all three implementations for one contract + behaviour."""
    def __init__(self, tmp, contract=CONTRACT, behaviour=BEHAVIOUR, base_path=""):
        self.tmp, self.contract, self.behaviour = pathlib.Path(tmp), contract, behaviour
        (self.tmp / "contract.json").write_text(json.dumps(contract))
        (self.tmp / "behaviour.json").write_text(json.dumps(behaviour))
        build_worker.build(self.tmp / "contract.json", self.tmp / "behaviour.json", self.tmp / "out", base_path=base_path)
        (self.tmp / "config.json").write_text(json.dumps(build_worker.worker_config(contract, behaviour, base_path)))
        self.n = 0

    def _scenario(self, steps):
        self.n += 1
        p = self.tmp / f"scenario{self.n}.json"
        p.write_text(json.dumps(steps))
        return p

    def core(self, steps):
        return _node("core", CORE, self.tmp / "config.json", self._scenario(steps))

    def worker(self, steps):
        return [r if (r and isinstance(r[0], list)) else r[:2] for r in _node("worker", self.tmp / "out/worker.js", self._scenario(steps))]

    def python(self, steps):
        m = ms.Mock(json.loads(json.dumps(self.contract)), json.loads(json.dumps(self.behaviour)))
        out = []
        for s in steps:
            if s.get("control") == "reset":
                m.reset(s.get("session"))
                out.append([200, {"ok": True}])
            elif s.get("control") == "ledger":
                out.append([200, {"ledger": m.ledger(s.get("session"))}])
            else:
                st, body = m.handle(s["method"], s["path"], {k.lower(): v for k, v in (s.get("headers") or {}).items()},
                                    s.get("body"), s.get("query") or {})
                out.append([st, body])
        return json.loads(json.dumps(out))

    def all_three(self, steps):
        py, core, worker = self.python(steps), self.core(steps), self.worker(steps)
        assert core == py
        assert worker == [[st, None] if st == 204 else [st, body] for st, body in py]   # HTTP 204 carries no body
        return py


def post(path, body, **kw):
    return {"method": "POST", "path": path, "body": body, **kw}


@pytest.fixture
def run(tmp_path):
    return Run(tmp_path)


def test_generated_project_is_self_contained_and_free_plan(tmp_path):
    Run(tmp_path)
    out = tmp_path / "out"
    src = (out / "worker.js").read_text()
    toml = (out / "wrangler.toml").read_text()
    assert [l.split("{")[0].strip() for l in src.splitlines() if l.startswith("export")] == ["export class MockState", "export default"]
    assert not any(l.startswith("import") for l in src.splitlines())
    assert 'new_sqlite_classes = ["MockState"]' in toml and 'class_name = "MockState"' in toml and "kv_namespaces" not in toml
    assert json.loads((out / "package.json").read_text())["type"] == "module"
    r = subprocess.run([NODE, "--check", str(out / "worker.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_validation_422_matches_the_reference(run):
    steps = [
        post("/close_call", {"customer_id": "C1", "outcome": "not_an_outcome"}),           # enum
        post("/create_promise", {"customer_id": "C1"}),                                     # required
        post("/create_promise", {"customer_id": "C1", "product_id": "p", "amount": "1", "date": "2026-10-20"}),   # type
        post("/create_promise", {"customer_id": "C1", "product_id": "p", "amount": 1, "date": "2026-02-30"}),     # date format
        {"method": "POST", "path": "/close_call"},                                           # body required
        {"control": "ledger", "session": "C1"},
    ]
    res = run.all_three(steps)
    assert [r[0] for r in res[:5]] == [422] * 5
    assert "outcome" in res[0][1]["error"] and "not one of" in res[0][1]["error"]
    assert "product_id" in res[1][1]["error"] and "required" in res[1][1]["error"]
    assert "amount" in res[2][1]["error"] and "date" in res[3][1]["error"]
    assert res[4][1] == {"error": "request body required"}
    assert all(e["rejected"] for e in res[5][1]["ledger"])


def test_unparseable_json_is_rejected_by_the_worker(run):
    res = run.worker([post("/close_call", "{not json", raw=True), {"control": "ledger", "session": "default"}])
    assert res[0][0] == 422 and "__unparseable__" in json.dumps(res[1][1])


def test_state_across_calls_verification_lockout_and_closed_call(run):
    wrong = post("/verify_identity", {"customer_id": "C2", "field": "phone_last4", "answer": "9999"})
    steps = [
        post("/get_account", {"customer_id": "C1"}),
        post("/verify_identity", {"customer_id": "C1", "field": "date_of_birth", "answer": "1980-05-14"}),
        post("/verify_identity", {"customer_id": "C1", "field": "phone_last4", "answer": "0042"}),
        post("/get_account", {"customer_id": "C1"}),
        wrong, wrong, wrong, wrong,
        post("/create_promise", {"customer_id": "C1", "product_id": "CC-1", "amount": 300, "date": "2026-10-20"}),
        post("/add_note", {"customer_id": "C1", "product_id": "CC-1", "reason": "hardship", "text": "lost job"}),
        post("/schedule_callback", {"customer_id": "C1", "date": "2026-10-21", "time": "14:30"}),
        post("/close_call", {"customer_id": "C1", "outcome": "promise"}),
        post("/close_call", {"customer_id": "C1", "outcome": "paid"}),
        post("/create_promise", {"customer_id": "C1", "product_id": "CC-1", "amount": 1, "date": "2026-10-22"}),
        {"control": "ledger", "session": "C1"},
    ]
    res = run.all_three(steps)
    assert res[0][1] == {"ok": False, "error": "not verified"}
    assert res[1][1]["all_verified"] is False and res[2][1]["all_verified"] is True
    assert res[3][1]["products"][0]["product_id"] == "CC-1"
    assert [r[1]["wrong_answers_left"] for r in res[4:8]] == [2, 1, 0, 0] and res[7][1]["error"] == "locked"
    assert res[8][1] == {"ok": True, "promise_id": "PR-C1-CC-1"}
    assert res[11][1] == {"ok": True} and res[12][1]["error"] == "call already closed" and res[13][1]["error"] == "call closed"
    recorded = [e["op"] for e in res[14][1]["ledger"] if e.get("recorded")]
    assert recorded == ["POST /verify_identity", "POST /verify_identity", "POST /create_promise", "POST /add_note",
                        "POST /schedule_callback", "POST /close_call"]


def test_fail_first_then_success_counter_is_per_session_and_reset(run):
    flaky = {"customer_id": "C1", "product_id": "flaky", "amount": 5, "date": "2026-10-20"}
    steps = [post("/create_promise", flaky), post("/create_promise", flaky), post("/create_promise", flaky),
             post("/create_promise", {**flaky, "customer_id": "C3"}),
             {"control": "ledger", "session": "C1"}, {"control": "reset", "session": "C1"},
             post("/create_promise", flaky), {"control": "ledger", "session": "C1"}]
    res = run.all_three(steps)
    assert [r[0] for r in res[:4]] == [500, 500, 200, 500]
    assert res[0][1] == {"error": "injected failure"}
    assert [e.get("recorded") for e in res[4][1]["ledger"]] == [False, False, True]
    assert res[6][0] == 500 and len(res[7][1]["ledger"]) == 1


def test_sessions_are_isolated_and_reset_all(run):
    p = {"product_id": "X", "amount": 5, "date": "2026-10-20"}
    steps = [post("/create_promise", {"customer_id": "A", **p}), post("/close_call", {"customer_id": "A", "outcome": "promise"}),
             post("/close_call", {"customer_id": "B", "outcome": "no_promise", "reason": "refused"}),
             post("/create_promise", {"customer_id": "B", **p}),
             {"control": "ledger", "session": "A"}, {"control": "ledger", "session": "B"}]
    res = run.all_three(steps)
    assert res[3][1]["ok"] is False                    # B is closed, A's state untouched
    assert [e["session"] for e in res[4][1]["ledger"]] == ["A", "A"] and len(res[5][1]["ledger"]) == 2
    w = run.worker(steps[:4] + [{"control": "ledger"}, {"control": "reset"}, {"control": "ledger"},
                                post("/close_call", {"customer_id": "A", "outcome": "paid"})])
    assert sorted(w[4][1]["ledger"]) == ["A", "B"]
    assert w[6][1] == {"ledger": {}} and w[7][1] == {"ok": True}   # A's closed state is gone after the global reset


def test_missing_behaviour_unknown_path_and_off_contract_response(tmp_path):
    b = json.loads(json.dumps(BEHAVIOUR))
    b["operations"]["POST /add_note"] = [{"body": {"ok": "yes"}}]
    r = Run(tmp_path, behaviour=b)
    res = r.all_three([post("/transfer_to_agent", {"customer_id": "C1", "queue": "general"}),
                       post("/nope", {"customer_id": "C1"}),
                       post("/add_note", {"customer_id": "C1", "product_id": "p", "reason": "paid"})])
    assert res[0][0] == 501 and "no behaviour" in res[0][1]["error"]
    assert res[1][0] == 404
    assert res[2][0] == 500 and "misconfigured" in res[2][1]["error"]


def _contract(paths, components=None):
    return {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": paths, "components": components or {}}


def _op(schema=None, responses=None, op_id=None, required=True, method_params=None):
    op = {"responses": responses or {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object"}}}}}}
    if schema is not None:
        op["requestBody"] = {"required": required, "content": {"application/json": {"schema": schema}}}
    if op_id:
        op["operationId"] = op_id
    return op


def test_routing_path_params_concrete_first_query_and_header_session(tmp_path):
    c = _contract({"/accounts/{id}": {"get": _op(op_id="byId")}, "/accounts/search": {"get": _op(op_id="search")},
                   "/customers/{cid}/verify": {"post": _op({"type": "object", "required": ["a"]}, op_id="verify")}})
    b = {"session_key": {"header": "X-Customer"}, "fixtures": {"c1": {"x": 1}, "*": {"x": 0}},
         "operations": {"byId": [{"when": {"$path.id": "7"}, "body": {"id": "{{path.id}}", "x": "{{state.x}}"}}],
                        "search": [{"body": {"q": "{{query.q}}"}}],
                        "verify": [{"body": {"cid": "{{path.cid}}", "a": "{{body.a}}"}, "set": {"x": "{{body.a}}"}}]}}
    r = Run(tmp_path, c, b)
    h = {"X-Customer": "c1"}
    res = r.all_three([{"method": "GET", "path": "/accounts/search", "headers": h, "query": {"q": "abc"}},
                       {"method": "GET", "path": "/accounts/7", "headers": h},
                       {"method": "GET", "path": "/accounts/8", "headers": h},
                       post("/customers/C77/verify", {"a": 5}, headers=h),
                       {"method": "GET", "path": "/accounts/7", "headers": h},
                       {"method": "GET", "path": "/accounts/7", "headers": {"X-Customer": "c2"}},
                       {"method": "DELETE", "path": "/accounts/7", "headers": h}])
    assert res[0] == [200, {"q": "abc"}] and res[1] == [200, {"id": "7", "x": 1}] and res[2][0] == 501
    assert res[3] == [200, {"cid": "C77", "a": 5}] and res[4] == [200, {"id": "7", "x": 5}] and res[5] == [200, {"id": "7", "x": 0}]
    assert res[6][0] == 404
    assert r.worker([post("/customers/C%2077/verify", {"a": 1}, headers=h)])[0] == [200, {"cid": "C 77", "a": 1}]   # decoded


def test_base_path_prefix_is_optional_and_control_endpoints_stay_at_root(tmp_path):
    r = Run(tmp_path, base_path="/v1")
    res = r.worker([post("/v1/close_call", {"customer_id": "Z", "outcome": "paid"}),
                    post("/close_call", {"customer_id": "Z", "outcome": "paid"}),
                    {"control": "ledger", "session": "Z"}, {"control": "health"}])
    assert res[0] == [200, {"ok": True}] and res[1][1]["error"] == "call already closed"
    assert [e["path"] for e in res[2][1]["ledger"]] == ["/close_call", "/close_call"]
    assert res[3][1]["base_path"] == "/v1" and len(res[3][1]["operations"]) == 7
    assert r.worker([post("/v2/close_call", {"customer_id": "Z", "outcome": "paid"})])[0][0] == 404


def test_formats_nullable_bounds_and_optional_body_match_the_reference(tmp_path):
    schema = {"type": "object", "properties": {
        "d": {"type": "string", "format": "date"}, "t": {"type": "string", "format": "time"},
        "dt": {"type": "string", "format": "date-time"},
        "amount": {"type": "number", "minimum": 0, "maximum": 100, "exclusiveMaximum": True},
        "addr": {"nullable": True, "allOf": [{"$ref": "#/components/schemas/A"}]},
        "n": {"type": "integer", "nullable": True}, "nullable": {"type": "boolean"},
        "kids": {"type": "array", "items": {"$ref": "#/components/schemas/N"}}}}
    comps = {"schemas": {"A": {"type": "object", "required": ["city"]},
                         "N": {"type": "object", "properties": {"kids": {"type": "array", "items": {"$ref": "#/components/schemas/N"}}}}}}
    c = _contract({"/p": {"post": _op(schema, op_id="p")}, "/q": {"post": _op({"type": "object", "required": ["a"]}, op_id="q", required=False)}},
                  comps)
    r = Run(tmp_path, c, {"operations": {"p": [{"body": {}}], "q": [{"body": {}}]}})
    vals = [{"d": "2026-10-20"}, {"d": "2026-13-01"}, {"d": "2024-02-29"}, {"d": "2025-02-29"}, {"d": "20261020"},
            {"t": "14:30"}, {"t": "14:30:00+02:00"}, {"t": "24:00"}, {"t": "9:30"}, {"t": "14:30:00.5Z"},
            {"dt": "2026-10-20T11:00"}, {"dt": "2026-10-20T11:00:00Z"}, {"dt": "2026-10-20T11:00:00+03:00"},
            {"dt": "2026-10-20"}, {"dt": "garbage"}, {"dt": "2026-10-20T25:00"},
            {"amount": 50}, {"amount": 100}, {"amount": -1}, {"amount": True},
            {"addr": None}, {"addr": {"city": "X"}}, {"addr": {}}, {"n": None}, {"n": 1.0}, {"n": 1.5},
            {"nullable": "no"}, {"kids": [{"kids": [{}]}]}, {"kids": [1]}]
    steps = [post("/p", v) for v in vals] + [{"method": "POST", "path": "/q"}, post("/q", {})]
    res = r.all_three(steps)
    got = [x[0] for x in res]
    assert got[:5] == [200, 422, 200, 422, 422] and got[-2:] == [200, 422]


def test_204_without_body_and_template_errors(tmp_path):
    resp = {"204": {"description": "done"}}
    c = _contract({"/d": {"post": _op(op_id="d", responses=resp)}, "/o": {"post": _op(op_id="o")}})
    b = {"fixtures": {"*": {"log": None}}, "operations": {
        "d": [{"status": 204, "body": {}}],
        "o": [{"when": {"k": 2}, "body": {"ref": "R-{{body.missing}}"}}, {"when": {"k": 4}, "body": {}, "append": {"log": "{{body}}"}},
              {"body": {"s": "{{body.k}}-{{body.b}}"}}]}}
    r = Run(tmp_path, c, b)
    res = r.all_three([{"method": "POST", "path": "/d"}, post("/o", {"k": 2}), post("/o", {"k": 4}), post("/o", {"k": 1, "b": True})])
    assert res[0][0] == 204 and res[1][0] == 500 and "body.missing" in res[1][1]["error"]
    assert res[2][0] == 500 and "not a list" in res[2][1]["error"] and res[3][1] == {"s": "1-True"}
    assert r.worker([{"method": "POST", "path": "/d"}])[0] == [204, None]


def test_concurrent_calls_on_one_session_are_serialized(run):
    par = [post("/create_promise", {"customer_id": "C9", "product_id": f"P{i}", "amount": i, "date": "2026-10-20"}) for i in range(20)]
    res = run.worker([{"parallel": par}, {"control": "ledger", "session": "C9"}, {"control": "ledger"}])
    assert [r[0] for r in res[0]] == [200] * 20
    assert sorted(e["body"]["product_id"] for e in res[1][1]["ledger"]) == sorted(f"P{i}" for i in range(20))
    assert list(res[2][1]["ledger"]) == ["C9"]


def test_yaml_contract_is_converted(tmp_path):
    if not shutil.which("ruby"):
        pytest.skip("no ruby")
    (tmp_path / "c.yaml").write_text("openapi: 3.0.3\ninfo: {title: t, version: '1'}\npaths:\n  /ping:\n    post:\n"
                                     "      operationId: ping\n      requestBody:\n        required: true\n        content:\n"
                                     "          application/json:\n            schema: {type: object, required: [when], properties: {when: {type: string, format: date}}}\n"
                                     "      responses:\n        '200': {description: ok}\n")
    (tmp_path / "b.json").write_text(json.dumps({"operations": {"ping": [{"body": {"pong": "{{body.when}}"}}]}}))
    build_worker.build(tmp_path / "c.yaml", tmp_path / "b.json", tmp_path / "out")
    (tmp_path / "s.json").write_text(json.dumps([post("/ping", {"when": "2026-10-20"}), post("/ping", {"when": "x"})]))
    res = _node("worker", tmp_path / "out/worker.js", tmp_path / "s.json")
    assert res[0][:2] == [200, {"pong": "2026-10-20"}] and res[1][0] == 422


def test_cli_strict_fails_on_behaviour_problems(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "devtools/cf_mock/build_worker.py"), str(ROOT / "kit/tools/openapi.json"),
                        str(ROOT / "tests/fixtures/cf_mock/behaviour.json"), str(tmp_path / "o"), "--strict"], capture_output=True, text=True)
    assert r.returncode != 0 and "transfer_to_agent" in r.stderr
