"""Generalisation: our own raw-material brief R1 (memo + code list) on a mutated API (tests/variants/)."""
import copy, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder import openapi_tools as ot, compile_workflow as cw, validate as v, local_exec as le, policy_schema as ps

BRIEF = (ROOT / "tests/variants/brief-r1.md").read_text()
IR = {k: x for k, x in json.load(open(ROOT / "tests/variants/ir-r1.ref.json")).items() if not k.startswith("_")}
API = ot.load(str(ROOT / "tests/variants/api-r1.json"))
ROLES, _ = ot.infer_roles(API)
PRODS = [{"product_id": "p1", "type": "card", "card_last4": "7310", "balance_overdue": 300.0, "days_overdue": 20, "min_due": 60.0},
         {"product_id": "p2", "type": "auto_loan", "balance_overdue": 900.0, "days_overdue": 70, "monthly_payment": 250.0}]


def compiled(ir=IR):
    return cw.compile_workflow(ir, API, ROLES)


def test_r1_reference_passes_checks_and_lint():
    assert ps.check(IR, API, ROLES, BRIEF) == []
    wf = compiled()
    assert v.lint(wf, API) == []


def test_now_variable_comes_from_the_brief():
    wf = compiled()
    code = "".join(src for _, src in v.code_snippets(wf))
    assert "vars.get('now')" not in code
    assert "{{call_time}}" in wf["system_prompt"] and "call_time" in wf["test_variables"]
    r = le.smoke(wf, ROLES, PRODS, now="2026-10-14T11:00", now_var="call_time")
    assert r["init_ok"] == "yes" and r["account_ok"] == "yes"
    assert "card ending 7310" in r["debt_statement"] and "auto loan" in r["debt_statement"]


def test_promise_uses_api_field_names_and_outcome_codes():
    wf = compiled()
    # band B (15-60): 5th business day after Wed 2026-10-14 = Wed 2026-10-21
    r = le.smoke(wf, ROLES, PRODS, now="2026-10-14T11:00", now_var="call_time", promise=("p1", 300.0, "2026-10-21"))
    assert r["promise_sent"] == {"customer_id": "c_local", "product_id": "p1", "amount": 300.0, "pay_date": "2026-10-21"}
    assert r["close_body"]["outcome"] == "PTP" and "reason" not in r["close_body"]
    late = le.smoke(wf, ROLES, PRODS, now="2026-10-14T11:00", now_var="call_time", promise=("p1", 300.0, "2026-10-22"))
    assert "promise_blocked" in late


def close_for(state, wf):
    vars = {"st_json": json.dumps(state), "customer_id": "c1"}
    return le.run_pre(wf["tools"][0], vars, {"customer_id": "c1", "outcome": "", "reason": ""})


def test_reasons_are_mapped_once_through_the_code_list():
    wf = compiled()
    base = {"products": {}, "promises": {}, "notes": {}, "order": ["p1", "p2"], "transfer": "", "verified": "yes", "contact": "customer"}
    assert close_for({**base, "transfer": "support"}, wf) == {"customer_id": "c1", "outcome": "XFER", "reason": "WEL"}
    assert close_for({**base, "verified": "declined"}, wf)["reason"] == "REF"
    assert close_for({**base, "verified": "failed"}, wf) == {"customer_id": "c1", "outcome": "NOID", "reason": "FAIL"}
    assert close_for({**base, "contact": "callback"}, wf) == {"customer_id": "c1", "outcome": "NOCONTACT", "reason": "CB"}
    assert close_for({**base, "notes": {"p1": "paid", "p2": "hardship"}}, wf) == {"customer_id": "c1", "outcome": "NOPTP", "reason": "HRD"}
    assert close_for({**base, "notes": {"p1": "paid", "p2": "paid"}}, wf) == {"customer_id": "c1", "outcome": "PAID"}


def test_placeholder_may_name_the_variable_or_its_role():
    ir = copy.deepcopy(IR)
    ir["opening"]["identity_question"] = "May I speak to <salutation> <last_name>, please?"
    wf = compiled(ir)
    assert "May I speak to {{salutation}} {{last_name}}, please?" in wf["nodes"]["opening"]["on_enter"]["text"]


def test_postcode_said_in_words_is_normalised():
    ns = {}
    exec((ROOT / "builder/runtime_src.py").read_text(), ns)
    assert ns["normalize_answer"]("upper_nospace", "S W one A, one A A") == "SW1A1AA"
    assert ns["normalize_answer"]("digits4", "nineteen eighty-eight") == "1988"
