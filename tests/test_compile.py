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


def test_refusal_ladder_today_is_idempotent_and_offers_count():
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), IR)
    vars = {**wf["test_variables"], "now": "2026-10-14T11:00"}
    le.run_code_node(wf, "init", vars)
    prods = [{"product_id": "p1", "type": "credit_card", "card_last4": "9015", "past_due": 510, "days_late": 16, "minimum_payment": 153},
             {"product_id": "p2", "type": "personal_loan", "past_due": 1320, "days_late": 16, "instalment_amount": 440},
             {"product_id": "p3", "type": "car_loan", "past_due": 140, "days_late": 7, "instalment_amount": 46}]
    vars.update(le.flatten_products("load_account_response.data.products", prods))
    le.run_code_node(wf, "account", vars)
    tool = le._tool(wf, "negotiate", "record_refusal")

    def refuse(pid, kind):
        try:
            le.run_pre(tool, vars, {"product_id": pid, "refused": kind})
        except le.Blocked as b:
            return str(b)
        raise AssertionError("oracle must always raise")
    assert "attempt 1 of 1 for p3" in refuse("p3", "today")
    again = refuse("p3", "today")      # PUB-12 c1 failure: a second 'today' used up the car loan's only attempt
    assert "already recorded" in again and "attempt 1 of 1 for p3" in again and vars["att__p3"] == "1"
    assert "no persuasion attempt left for p3" in refuse("p3", "offer")
    assert "attempt 1 of 2 for p2" in refuse("p2", "today")
    assert "attempt 2 of 2 for p2" in refuse("p2", "offer")
    assert "no persuasion attempt left for p2" in refuse("p2", "offer")


def test_pinned_sdk_matches_pyproject():
    import re as _re
    sys.path.insert(0, str(ROOT / "builder"))
    import build
    pin = _re.search(r'claude-agent-sdk==([0-9.]+)', (ROOT / "pyproject.toml").read_text()).group(1)
    assert build.PINNED_SDK == pin


def test_optional_tools_may_be_absent():
    spec = json.load(open(ROOT / "kit/tools/openapi.json"))
    for t in ("add_note", "schedule_callback", "transfer_to_agent"):
        spec["paths"].pop("/" + t)
    api = ot.load(spec)
    assert api["missing_tools"] == [] and sorted(api["absent_optional"]) == ["add_note", "schedule_callback", "transfer_to_agent"]
    roles, problems = ot.infer_roles(api)
    assert problems == [], problems
    warns = []
    wf = cw.compile_workflow(IR, api, roles, warnings=warns)
    assert v.lint(wf, api) == [] and warns
    blob = json.dumps(wf)
    assert "__absent__" not in blob and "transfer_human" not in wf["nodes"] and "write_note" not in wf["nodes"]
    names = {t["function"]["name"] for n in wf["nodes"].values() for t in (n.get("tools") or []) + (n.get("pre_actions") or [])}
    assert not names & {"add_note", "schedule_callback", "transfer_to_agent"}
    prods = [{"product_id": "p1", "type": "credit_card", "card_last4": "1111", "past_due": 300, "days_late": 4, "minimum_payment": 90}]
    r = le.smoke(wf, roles, prods, promise=("p1", 300, "2026-10-16"))
    assert r["close_body"]["outcome"] == "promise"


def _negotiation_vars(wf, prods):
    vars = {**wf["test_variables"], "now": "2026-10-14T11:00"}
    le.run_code_node(wf, "init", vars)
    vars.update(le.flatten_products("load_account_response.data.products", prods))
    le.run_code_node(wf, "account", vars)
    return vars


def _oracle(wf, node, name, vars, body):
    try:
        le.run_pre(le._tool(wf, node, name), vars, body)
    except le.Blocked as b:
        return str(b)
    raise AssertionError("oracle must raise")


TWO = [{"product_id": "p1", "type": "credit_card", "card_last4": "9015", "past_due": 300, "days_late": 4, "minimum_payment": 90},
       {"product_id": "p2", "type": "personal_loan", "past_due": 900, "days_late": 20, "instalment_amount": 300}]


def test_identity_completes_when_the_bank_omits_all_verified():
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), IR)
    tool = le._tool(wf, "verify", "verify_identity")
    vars = {}
    le.run_pre(tool, vars, {"customer_id": "c", "field": "date_of_birth", "answer": "1988-03-14"})
    le.run_post(tool, vars, {"ok": True, "verified": True, "wrong_answers_left": 3})
    assert vars["id_state"] == "next"
    le.run_pre(tool, vars, {"customer_id": "c", "field": "phone_last4", "answer": "4471"})
    le.run_post(tool, vars, {"ok": True, "verified": True, "wrong_answers_left": 3})   # no all_verified key (already-verified customer)
    assert vars["id_state"] == "all"


def test_finish_call_is_guarded_once_and_sets_the_route_variable():
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), IR)
    vars = _negotiation_vars(wf, TWO)
    first = _oracle(wf, "negotiate", "finish_call", vars, {"summary": "x"})
    assert first.startswith("BLOCKED") and "p1, p2" in first and vars.get("wrap_req") != "yes"
    assert _oracle(wf, "negotiate", "finish_call", vars, {"summary": "x"}).startswith("FINISHED") and vars["wrap_req"] == "yes"
    assert any(t.get("condition", {}).get("variable") == "wrap_req" or "wrap_req" in json.dumps(t) for t in wf["nodes"]["negotiate"]["transitions"])
    assert not any(t.get("is_tool_call") and t["target_node"] == "wrap_up" for n in ("negotiate", "post_record") for t in wf["nodes"][n]["transitions"])


def test_vague_words_ignore_the_models_own_date():
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), IR)
    vars = _negotiation_vars(wf, TWO[:1])
    msg = _oracle(wf, "negotiate", "check_date", vars, {"product_id": "p1", "customer_words": "the week after next", "date": "2026-10-26"})
    assert "no exact day" in msg and "26 October is not" not in msg


def test_fold_fallbacks_never_send_an_empty_no_promise_reason():
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), IR)
    close = lambda st: le.run_pre(wf["tools"][0], {"st_json": json.dumps(st)}, {"customer_id": "c1", "outcome": "", "reason": ""})
    base = {"products": {}, "promises": {}, "notes": {}, "order": [], "transfer": "", "verified": "", "contact": ""}
    assert close(base) == {"customer_id": "c1", "outcome": "not_reached", "reason": "unclear"}
    assert close({**base, "contact": "customer"}) == {"customer_id": "c1", "outcome": "not_verified", "reason": "declined"}
    assert close({**base, "contact": "customer", "verified": "yes"}) == {"customer_id": "c1", "outcome": "no_promise", "reason": "refused"}


def test_incomplete_identity_answers_are_not_sent():
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), IR)
    tool = le._tool(wf, "verify", "verify_identity")
    for field, answer in (("phone_last4", "44"), ("phone_last4", "four four"), ("date_of_birth", "14 March")):
        try:
            le.run_pre(tool, {}, {"customer_id": "c", "field": field, "answer": answer})
            raise AssertionError("must not be sent: " + answer)
        except le.Blocked as b:
            assert str(b).startswith("NOT SENT")
    assert le.run_pre(tool, {}, {"customer_id": "c", "field": "phone_last4", "answer": "four four seven one"})["answer"] == "4471"
    assert le.run_pre(tool, {}, {"customer_id": "c", "field": "date_of_birth", "answer": "14 March 1988"})["answer"] == "1988-03-14"


def test_attempt_one_date_never_after_a_short_deadline():
    ir = copy.deepcopy(IR)
    ir["bands"] = [{"name": "only", "min": 1, "max": 90, "deadline_business_days": 1, "deadline_unit": "business_days", "attempts": 1}]
    ir["ladder"] = [{"attempt": 1, "amount": "full_or_floor", "date": "end_of_week", "bands": []}]
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), ir)
    prods = [{"product_id": "p1", "type": "credit_card", "card_last4": "1", "past_due": 300, "days_late": 4, "minimum_payment": 90}]
    p = le.smoke(wf, roles, prods)["state"]["products"]["p1"]     # Wed 14 Oct: deadline Thu 15, end of week Fri 16
    assert p["deadline"] == "2026-10-15" and p["attempt1_date"] == "2026-10-15"


def test_c9_ported_rules_are_in_the_prompts():
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), IR)
    verify, neg = wf["nodes"]["verify"]["task_message"], wf["nodes"]["negotiate"]["task_message"]
    assert "the words after it were cut off: call verify_identity" in verify and "A wrong answer that you do not send" in verify
    # fragments: re-ask the pending question at once (practice-call study: better than backchannels), never a backchannel
    assert "reply at once by asking the pending question again in one short, slightly rephrased sentence" in verify
    assert "never add the refusal explanation to this re-ask" in verify and "Never invent or complete digits" in verify
    assert "reply at once by asking your pending question again in one short, slightly rephrased sentence; call no tool and make no new offer" in neg
    assert "Go on." not in verify + neg and "Take your time." not in verify + neg and "two such replies" not in verify + neg
    assert "join everything the customer said since you last asked" in verify
    assert "never complete it yourself" in neg and "everything the rules attach to a floor offer" in neg
    assert "including products you already marked as refused" in neg


def test_undescribed_hint_is_not_reported_as_a_wrong_answer():
    ir = copy.deepcopy(IR)
    ir["identity"]["swapped_hint_value"] = None                     # brief silent about the swap rule
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), ir)
    tool = le._tool(wf, "verify", "verify_identity")
    vars = {}
    le.run_pre(tool, vars, {"customer_id": "c", "field": "date_of_birth", "answer": "1988-14-03".replace("14-03", "03-14")})
    out = le.run_post(tool, vars, {"ok": True, "verified": False, "wrong_answers_left": 3, "hint": "day_month_swapped"})
    assert vars["id_state"] == "hint" and "not counted" in out["what_to_do_next"] and "wrong" not in out["what_to_do_next"].split("not counted")[0]


def test_bare_tens_day_words_are_never_completed():
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), IR)
    vars = _negotiation_vars(wf, TWO[:1])
    for words in ("I can pay that on the thirty", "the twenty", "on the thirty."):
        msg = _oracle(wf, "negotiate", "check_date", vars, {"product_id": "p1", "customer_words": words, "date": "2026-10-30"})
        assert "may have been cut off" in msg and "30 October is" not in msg
    ok = _oracle(wf, "negotiate", "check_date", vars, {"product_id": "p1", "customer_words": "the thirtieth", "date": "2026-10-30"})
    assert "cut off" not in ok


def test_lint_flags_control_characters_in_generated_code():
    api, roles, wf = build(json.load(open(ROOT / "kit/tools/openapi.json")), IR)
    assert v.lint(wf, api) == []
    tool = le._tool(wf, "negotiate", "check_date")
    tool["pre_process_code"] = tool["pre_process_code"].replace("import re", "import re\nx = '\b'", 1)
    assert any("control character" in p for p in v.lint(wf, api))
