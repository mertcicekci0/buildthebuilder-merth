import copy, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from builder import openapi_tools as ot, compile_workflow as cw, validate as v, local_exec as le, policy_schema as ps
from test_openapi_tools import mutated

IR = {k: x for k, x in json.load(open(ROOT / "tests/fixtures/ir_public_ab.json")).items() if not k.startswith("_")}

def build(api_spec, ir):
    api = ot.load(api_spec); roles, probs = ot.infer_roles(api)
    assert probs == [], probs
    return api, roles, cw.compile_workflow(ir, api, roles)

def test_public_compile_is_clean_and_self_tests():
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), IR)
    assert v.lint(wf, api) == []
    assert "{{" not in "".join(src for _, src in v.code_snippets(wf))

def test_changed_api_compiles_with_its_own_names():
    ir = copy.deepcopy(IR)
    ir["identity"]["questions"][0].update(api_value="postcode", format="upper_nospace")
    ir["identity"]["questions"][1].update(api_value="birth_year", format="digits4")
    ir["outcomes"].update(promise="ptp", no_promise="no_ptp", not_verified="id_failed", not_reached="no_contact")
    ir["product_types"][0]["spoken"] = "credit card ending <card_last4>"
    api, roles, wf = build(mutated(), ir)
    assert v.lint(wf, api) == [], v.lint(wf, api)
    assert ps.check(ir, api, roles) == []
    rec = wf["nodes"]["write_promise"]["pre_actions"][0]["function"]["parameters"]["properties"]
    assert set(rec["body"]["properties"]) == {"customer_id", "product_id", "promised_amount", "payment_date"}
    assert rec["url"]["default"] == "https://bank.example.test/v9/create_promise"
    prods = [{"product_id": "p1", "type": "credit_card", "card_last4": "1111", "amount_overdue": 300, "days_past_due": 4, "minimum_payment": 90}]
    r = le.smoke(wf, roles, prods, promise=("p1", 300, "2026-10-16"))
    assert r["promise_sent"] == {"customer_id": "c_local", "product_id": "p1", "promised_amount": 300.0, "payment_date": "2026-10-16"}
    assert r["close_body"]["outcome"] == "ptp" and "reason" not in r["close_body"]
    assert "credit card ending 1111" in r["debt_statement"]
    assert r["record_summary"].startswith("Your promise is recorded: 300 for your credit card ending 1111 on Friday 16 October.")
    blocked = le.smoke(wf, roles, prods, promise=[("p1", 300, "2026-10-16"), ("p1", 50, "2026-10-19")])
    assert "already" in blocked.get("promise_blocked", "") or "below the floor" in blocked.get("promise_blocked", "")

def test_ir_with_value_not_in_api_is_rejected():
    ir = copy.deepcopy(IR); ir["outcomes"]["promise"] = "promised"
    api = ot.load(json.load(open(ROOT / "kit/tools/openapi.json"))); roles, _ = ot.infer_roles(api)
    assert any("promise" in p for p in ps.check(ir, api, roles))
    try:
        cw.compile_workflow(ir, api, roles); assert False
    except cw.CompileError:
        pass


def test_public_close_sends_no_reason_for_paid_and_maps_transfers():
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), IR)
    base = {"products": {}, "promises": {}, "notes": {}, "order": ["p1", "p2"], "transfer": "", "verified": "yes", "contact": "customer"}
    close = lambda st: le.run_pre(wf["tools"][0], {"st_json": json.dumps({**base, **st})}, {"customer_id": "c1", "outcome": "", "reason": ""})
    assert close({"notes": {"p1": "paid", "p2": "paid"}}) == {"customer_id": "c1", "outcome": "paid"}
    assert close({"notes": {"p1": "paid", "p2": "refused"}}) == {"customer_id": "c1", "outcome": "no_promise", "reason": "refused"}
    assert close({"promises": {"p1": {}}, "transfer": "human"}) == {"customer_id": "c1", "outcome": "transferred", "reason": "human"}
    assert close({"verified": "declined"}) == {"customer_id": "c1", "outcome": "not_verified", "reason": "declined"}
