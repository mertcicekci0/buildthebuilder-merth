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
