"""devtools/mock_service.py: the contract decides validity, the behaviour file decides answers. The kit tool API is only a stand-in
contract here; the final case's contract and behaviour are written after release."""
import json, pathlib, sys, threading, urllib.request
import pytest
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "devtools"))
import mock_service as ms

CONTRACT = json.loads((ROOT / "kit/tools/openapi.json").read_text())
BEHAVIOUR = {
    "session_key": {"body": "customer_id"},
    "fixtures": {"*": {"promises": []}},
    "operations": {
        "POST /create_promise": [
            {"when": {"product_id": "fail"}, "fail_first": 1, "status": 200, "body": {"ok": True}, "record": True},
            {"status": 200, "body": {"ok": True}, "append": {"promises": "{{body}}"}, "record": True},
        ],
        "POST /close_call": [{"status": 200, "body": {"ok": True}, "set": {"closed": "{{body.outcome}}"}, "record": True}],
    },
}


def mock():
    return ms.Mock(CONTRACT, json.loads(json.dumps(BEHAVIOUR)))


def test_every_contract_operation_is_served():
    assert len(mock().ops) == len(CONTRACT["paths"])


def test_invalid_requests_are_rejected_by_the_contract_and_recorded_as_rejected():
    m = mock()
    st, body = m.handle("POST", "/close_call", {}, {"customer_id": "c1", "outcome": "not_an_outcome"})
    assert st == 422 and "outcome" in body["error"]
    st, _ = m.handle("POST", "/create_promise", {}, {"customer_id": "c1"})      # required fields missing
    assert st == 422
    assert all(e.get("rejected") for e in m.ledger("c1"))


def test_writes_are_recorded_per_session_and_sessions_are_isolated():
    m = mock()
    ok = {"customer_id": "c1", "product_id": "p1", "amount": 100, "date": "2026-10-20"}
    assert m.handle("POST", "/create_promise", {}, ok)[0] == 200
    assert m.handle("POST", "/close_call", {}, {"customer_id": "c1", "outcome": "promise"})[0] == 200
    assert m.sessions["c1"]["state"]["promises"] == [ok] and m.sessions["c1"]["state"]["closed"] == "promise"
    assert [e["op"] for e in m.ledger("c1") if e.get("recorded")] == ["POST /create_promise", "POST /close_call"]
    assert m.ledger("c2") == [] and m.sessions["c2"]["state"] == {"promises": []}


def test_injected_failure_then_success_and_reset():
    m = mock()
    req = {"customer_id": "c1", "product_id": "fail", "amount": 100, "date": "2026-10-20"}
    assert m.handle("POST", "/create_promise", {}, req)[0] == 500
    assert m.handle("POST", "/create_promise", {}, req)[0] == 200
    m.reset("c1")
    assert m.handle("POST", "/create_promise", {}, req)[0] == 500       # the failure counter is per session and reset


def test_missing_behaviour_is_explicit_not_a_silent_success():
    st, body = mock().handle("POST", "/get_account", {}, {"customer_id": "c1"})
    assert st == 501 and "no behaviour" in body["error"]
    assert "no behaviour for POST /get_account" in mock().check()


def test_off_contract_responses_are_refused():
    b = json.loads(json.dumps(BEHAVIOUR))
    b["operations"]["POST /close_call"] = [{"status": 200, "body": {"ok": "yes"}}]
    m = ms.Mock(CONTRACT, b)
    assert any("does not match the contract" in p for p in m.check())
    st, body = m.handle("POST", "/close_call", {}, {"customer_id": "c1", "outcome": "promise"})
    assert st == 500 and "misconfigured" in body["error"]


def test_unknown_paths_and_unknown_rule_names():
    b = json.loads(json.dumps(BEHAVIOUR))
    b["operations"]["POST /nope"] = []
    m = ms.Mock(CONTRACT, b)
    assert m.handle("POST", "/nope", {}, {})[0] == 404
    assert "behaviour for unknown operation POST /nope" in m.check()


def test_http_server_end_to_end():
    m = mock()
    srv = ms.ThreadingHTTPServer(("127.0.0.1", 0), ms.make_handler(m))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"content-type": "application/json"}, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        assert call("/__mock/health")[0] == 200
        assert call("/close_call", {"customer_id": "c9", "outcome": "promise"})[0] == 200
        assert call("/close_call", {"customer_id": "c9", "outcome": "bad"})[0] == 422
        led = call("/__mock/ledger?session=c9")[1]["ledger"]
        assert [e["status"] for e in led] == [200, 422]
        assert call("/__mock/reset", {"session": "c9"})[0] == 200 and call("/__mock/ledger?session=c9")[1]["ledger"] == []
    finally:
        srv.shutdown()


def test_date_and_time_formats_are_checked_by_the_mock_itself():
    from mock_service import FORMATS
    assert FORMATS.conforms("14:30", "time") and FORMATS.conforms("2026-10-20", "date")
    assert not FORMATS.conforms("25:00", "time") and not FORMATS.conforms("2026-13-01", "date")
    assert not FORMATS.conforms("garbage", "date-time") and FORMATS.conforms("2026-10-20T11:00", "date-time")
    m = mock()
    assert m.handle("POST", "/create_promise", {}, {"customer_id": "c1", "product_id": "p1", "amount": 1, "date": "2026-02-30"})[0] == 422


def test_an_injected_failure_records_nothing_and_changes_no_state():
    m = mock()
    req = {"customer_id": "c1", "product_id": "fail", "amount": 100, "date": "2026-10-20"}
    m.handle("POST", "/create_promise", {}, req)
    assert m.sessions["c1"]["state"]["promises"] == [] and not any(e.get("recorded") for e in m.ledger("c1"))


def _contract(paths, components=None):
    return {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "servers": [{"url": "https://x.test"}], "paths": paths,
            "components": components or {}}


def _op(schema=None, responses=None, op_id=None, required=True):
    op = {"responses": responses or {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object"}}}}}}
    if schema is not None:
        op["requestBody"] = {"required": required, "content": {"application/json": {"schema": schema}}}
    if op_id:
        op["operationId"] = op_id
    return op


def test_review_fixes_openapi30_bounds_nullable_and_optional_body():
    amount = {"type": "object", "properties": {"amount": {"type": "number", "minimum": 0, "maximum": 100, "exclusiveMaximum": True},
                                               "addr": {"nullable": True, "allOf": [{"$ref": "#/components/schemas/A"}]},
                                               "nullable": {"type": "boolean"}}, "required": ["nullable"]}
    c = _contract({"/p": {"post": _op(amount, op_id="p")}, "/q": {"post": _op({"type": "object", "required": ["a"]}, op_id="q", required=False)}},
                  {"schemas": {"A": {"type": "object"}}})
    m = ms.Mock(c, {"operations": {"p": [{"body": {}}], "q": [{"body": {}}]}})
    assert m.handle("POST", "/p", {}, {"amount": 50, "addr": None, "nullable": True})[0] == 200
    assert m.handle("POST", "/p", {}, {"amount": 100, "nullable": True})[0] == 422          # exclusive maximum
    assert m.handle("POST", "/p", {}, {"amount": 5, "nullable": "no"})[0] == 422           # property named 'nullable' still typed
    assert m.handle("POST", "/q", {}, None)[0] == 200                                      # optional body may be absent


def test_review_fixes_header_session_path_precedence_and_params():
    c = _contract({"/accounts/{id}": {"get": _op(op_id="byId")}, "/accounts/search": {"get": _op(op_id="search")}})
    b = {"session_key": {"header": "X-Customer"}, "fixtures": {"c1": {"x": 1}},
         "operations": {"byId": [{"when": {"$path.id": "7"}, "body": {"id": "{{path.id}}", "x": "{{state.x}}"}}],
                        "search": [{"body": {"q": "{{query.q}}"}}]}}
    m = ms.Mock(c, b)
    assert m.handle("GET", "/accounts/search", {"x-customer": "c1"}, None, {"q": "abc"}) == (200, {"q": "abc"})
    assert m.handle("GET", "/accounts/7", {"x-customer": "c1"}, None) == (200, {"id": "7", "x": 1})
    assert "c1" in m.sessions and "default" not in m.sessions


def test_review_fixes_response_codes_templates_and_append_errors(tmp_path):
    resp = {"204": {"description": "done"}, "default": {"description": "e", "content": {"application/json": {"schema": {"type": "object", "required": ["error"]}}}}}
    okresp = {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}}}}}}
    c = _contract({"/d": {"post": _op(op_id="d", responses=resp)}, "/o": {"post": _op(op_id="o", responses=okresp)}})
    b = {"fixtures": {"*": {"log": None}}, "operations": {
        "d": [{"status": 204, "body": {}}],
        "o": [{"when": {"k": 1}, "body": {"ok": "yes", "echo": "{{body.k}}"}},
              {"when": {"k": 2}, "body": {"ok": True, "ref": "R-{{body.missing}}"}},
              {"when": {"k": 3}, "status": 201, "body": {"ok": True}},
              {"when": {"k": 4}, "body": {"ok": True}, "append": {"log": "{{body}}"}}]}}
    led = tmp_path / "ledger.jsonl"
    m = ms.Mock(c, b, str(led))
    assert m.handle("POST", "/d", {}, None)[0] == 204
    problems = m.check()
    assert any("rule 0" in p and "does not match" in p for p in problems)      # static part of a templated body is checked
    assert any("rule 2" in p and "not declared" in p for p in problems)
    st, body = m.handle("POST", "/o", {}, {"k": 2})
    assert st == 500 and "body.missing" in body["error"]
    st, body = m.handle("POST", "/o", {}, {"k": 4})
    assert st == 500 and "not a list" in body["error"]
    m.reset("default")
    assert any('"reset"' in line for line in led.read_text().splitlines())


def test_review_fixes_recursive_refs_and_rfc3339_time():
    c = _contract({"/n": {"post": _op({"$ref": "#/components/schemas/N"}, op_id="n")}},
                  {"schemas": {"N": {"type": "object", "properties": {"children": {"type": "array", "items": {"$ref": "#/components/schemas/N"}},
                                                                     "t": {"type": "string", "format": "time"}}}}})
    m = ms.Mock(c, {"operations": {"n": [{"body": {}}]}})
    assert m.handle("POST", "/n", {}, {"children": [{"children": []}], "t": "14:30:00+02:00"})[0] == 200
    assert m.handle("POST", "/n", {}, {"t": "24:00"})[0] == 422
