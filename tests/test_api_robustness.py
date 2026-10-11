"""Hidden final APIs keep the seven tool names but may rename fields and values. These tests mutate the kit API (and use
the independently written R5/R6 APIs) and check that roles, hooks and required fields follow the file."""
import copy, datetime, json, pathlib, sys
import pytest
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "builder"))
from builder import openapi_tools as ot, compile_workflow as cw, validate as v, local_exec as le, policy_schema as ps
from builder.build import synthetic_products

KIT = json.loads((ROOT / "kit/tools/openapi.json").read_text())
IR = {k: x for k, x in json.loads((ROOT / "tests/fixtures/ir_public_ab.json").read_text()).items() if not k.startswith("_")}


def req(spec, tool):
    return spec["paths"]["/" + tool]["post"]["requestBody"]["content"]["application/json"]["schema"]


def resp(spec, tool):
    return spec["paths"]["/" + tool]["post"]["responses"]["200"]["content"]["application/json"]["schema"]


def rename(schema, old, new):
    schema["properties"] = {(new if k == old else k): x for k, x in schema["properties"].items()}
    if "required" in schema:
        schema["required"] = [new if k == old else k for k in schema["required"]]


def item(spec, key="products"):
    return resp(spec, "get_account")["properties"][key]["items"]


def realistic():
    """The kit API with the renames an audit found realistic: none of these names is in a name pattern's first choice."""
    s = copy.deepcopy(KIT)
    rename(req(s, "verify_identity"), "answer", "given_answer")
    rename(req(s, "close_call"), "outcome", "disposition")
    rename(req(s, "close_call"), "reason", "reason_code")
    rename(item(s), "type", "agreement_type")
    rename(item(s), "past_due", "outstanding_amount")
    rename(item(s), "days_late", "overdue_days")
    rename(resp(s, "get_account"), "products", "agreements")
    return s


def compiled(spec, ir=IR, warnings=None):
    api = ot.load(spec)
    roles, problems = ot.infer_roles(api)
    wf = cw.compile_workflow(ir, api, roles, warnings=warnings)
    return api, roles, problems, wf


def promise_on_deadline(wf, roles, prods, now_var="now"):
    pid = prods[0][roles["product_fields"]["product_id"]]
    dl = le.smoke(wf, roles, prods, now_var=now_var)["state"]["products"][pid]["deadline"]
    return pid, le.smoke(wf, roles, prods, promise=(pid, 500.0, dl), now_var=now_var)


# ------------------------------------------------------------------ role inference
def test_realistic_names_resolve_by_structure_and_compile():
    api, roles, problems, wf = compiled(realistic())
    assert problems == []
    assert (roles["verify_answer"], roles["close_outcome"], roles["close_reason"]) == ("given_answer", "disposition", "reason_code")
    assert roles["product_fields"] == {"product_id": "product_id", "product_type": "agreement_type", "past_due": "outstanding_amount",
                                       "days_late": "overdue_days"}
    assert roles["products_key"] == "agreements"
    assert ps.check(IR, api, roles) == [] and v.lint(wf, api) == []
    prods = [{"product_id": "p1", "agreement_type": "personal_loan", "outstanding_amount": 500.0, "overdue_days": 20, "instalment_amount": 120.0}]
    pid, r = promise_on_deadline(wf, roles, prods)
    assert r["account_ok"] == "yes" and "personal loan" in r["debt_statement"]
    assert set(r["promise_sent"]) == {"customer_id", "product_id", "amount", "date"} and r["promise_sent"]["product_id"] == "p1"
    assert r["close_body"]["disposition"] == "promise" and "outcome" not in r["close_body"]


@pytest.mark.parametrize("past_due,days_late", [("total_due", "days_in_arrears"), ("arrears", "dpd"), ("amount_overdue", "days_overdue"),
                                                ("balance", "days_past_due"), ("outstanding", "overdue_days")])
def test_product_amount_and_days_names(past_due, days_late):
    s = copy.deepcopy(KIT)
    rename(item(s), "past_due", past_due)
    rename(item(s), "days_late", days_late)
    roles, problems = ot.infer_roles(ot.load(s))
    assert problems == [] and (roles["product_fields"]["past_due"], roles["product_fields"]["days_late"]) == (past_due, days_late)


def test_product_id_prefers_a_reference_over_an_enum_code():
    s = copy.deepcopy(KIT)
    rename(item(s), "product_id", "account_ref")
    rename(item(s), "type", "product_code")
    for t in ("create_promise", "add_note"):
        rename(req(s, t), "product_id", "account_ref")
    roles, problems = ot.infer_roles(ot.load(s))
    assert problems == []
    assert roles["product_fields"]["product_id"] == "account_ref" and roles["product_fields"]["product_type"] == "product_code"
    assert roles["promise_product"] == roles["note_product"] == "account_ref"


def test_promise_product_shares_the_account_id_name():
    s = copy.deepcopy(KIT)
    rename(item(s), "product_id", "contract")
    rename(req(s, "create_promise"), "product_id", "contract")
    roles, _ = ot.infer_roles(ot.load(s))
    assert roles["product_fields"]["product_id"] == "contract" and roles["promise_product"] == "contract"
    assert "contract" in roles["guessed"]["promise_product"]


def test_two_arrays_pick_the_products_not_the_payment_history():
    s = copy.deepcopy(KIT)
    props = resp(s, "get_account")["properties"]
    products = props.pop("products")
    props["payment_history"] = {"type": "array", "items": {"type": "object", "properties": {
        "payment_id": {"type": "string"}, "amount": {"type": "number"}, "date": {"type": "string", "format": "date"}}}}
    props["alerts"] = {"type": "array", "items": {"type": "string"}}
    props["facilities"] = products
    api = ot.load(s)
    roles, problems = ot.infer_roles(api)
    assert problems == [] and roles["products_key"] == "facilities"
    assert "days_late" in ot.product_item_schema(api)["properties"]


def test_nested_products_array_is_found_and_read_with_its_full_path():
    s = copy.deepcopy(KIT)
    props = resp(s, "get_account")["properties"]
    products = props.pop("products")
    props["account"] = {"type": "object", "properties": {"holder": {"type": "string"}, "products": products}}
    props["payment_history"] = {"type": "array", "items": {"type": "object", "properties": {"amount": {"type": "number"}, "date": {"type": "string"}}}}
    s["components"] = {"schemas": {"Product": products["items"]}}
    products["items"] = {"$ref": "#/components/schemas/Product"}
    api, roles, problems, wf = compiled(s)
    assert problems == [] and roles["products_key"] == "account.products"
    assert v.lint(wf, api) == []
    code = wf["nodes"]["account"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    assert "load_account_response.data.account.products" in code
    prods = [{"product_id": "p1", "type": "car_loan", "past_due": 400.0, "days_late": 12, "instalment_amount": 200.0}]
    vars = {"customer_id": "c", "now": "2026-10-14T11:00"}
    le.run_code_node(wf, "init", vars)
    vars.update(le.flatten_products("load_account_response.data.account.products", prods))
    vars["load_account_response.data.payment_history.0.amount"] = 99.0
    le.run_code_node(wf, "account", vars)
    assert vars["account_ok"] == "yes" and vars["n_products"] == "1" and "car loan" in vars["debt_statement"]


def test_unrecognisable_roles_fail_with_a_clear_compile_error():
    s = copy.deepcopy(KIT)
    cc = req(s, "close_call")
    cc["properties"] = {"customer_id": {"type": "string"}, "alpha": {"type": "string"}, "beta": {"type": "string"}}
    api = ot.load(s)
    roles, problems = ot.infer_roles(api)
    assert any(p.startswith("close_outcome") for p in problems)
    with pytest.raises(cw.CompileError, match="close_outcome"):
        cw.compile_workflow(IR, api, roles)


# ------------------------------------------------------------------ response field names in hooks
def verify_spec(**renames):
    s = copy.deepcopy(KIT)
    for old, new in renames.items():
        rename(resp(s, "verify_identity"), old, new)
    return s


def verify_round(tool, vars, field, answer, reply):
    le.run_pre(tool, vars, {"customer_id": "c", "field": field, "answer": answer})
    return le.run_post(tool, vars, reply)["what_to_do_next"]


def test_renamed_verify_response_fields_drive_the_identity_check():
    api, roles, _, wf = compiled(verify_spec(verified="correct", wrong_answers_left="attempts_remaining", all_verified="fully_verified"))
    assert roles["responses"]["verify_identity"] == {"ok": "ok", "error": "error", "all_verified": "fully_verified", "verified": "correct",
                                                     "wrong_answers_left": "attempts_remaining", "hint": "hint"}
    tool = le._tool(wf, "verify", "verify_identity")
    vars = {}
    msg = verify_round(tool, vars, "date_of_birth", "1985-05-03", {"ok": True, "correct": True, "attempts_remaining": 3, "fully_verified": False})
    assert vars["id_state"] == "next" and "correct" in msg
    msg = verify_round(tool, vars, "phone_last4", "1234", {"ok": True, "correct": False, "attempts_remaining": 2, "fully_verified": False})
    assert vars["id_state"] == "wrong" and "2 wrong answer(s) left" in msg
    verify_round(tool, vars, "phone_last4", "4321", {"ok": True, "correct": True, "attempts_remaining": 2, "fully_verified": True})
    assert vars["id_state"] == "all"
    vars = {}
    verify_round(tool, vars, "phone_last4", "1111", {"ok": True, "correct": False, "attempts_remaining": 0})
    assert vars["id_state"] == "locked"


def test_verify_without_an_all_verified_flag_completes_when_every_question_passed():
    s = copy.deepcopy(KIT)
    rename(resp(s, "verify_identity"), "verified", "identity_ok")
    del resp(s, "verify_identity")["properties"]["all_verified"]
    _, roles, _, wf = compiled(s)
    assert roles["responses"]["verify_identity"]["verified"] == "identity_ok"
    tool = le._tool(wf, "verify", "verify_identity")
    vars = {}
    verify_round(tool, vars, "date_of_birth", "1985-05-03", {"ok": True, "identity_ok": True, "wrong_answers_left": 3})
    assert vars["id_state"] == "next"
    verify_round(tool, vars, "phone_last4", "1234", {"ok": True, "identity_ok": True, "wrong_answers_left": 3})
    assert vars["id_state"] == "all"


def flagless_spec():
    s = copy.deepcopy(KIT)
    resp(s, "create_promise")["properties"] = {"promise_id": {"type": "string"}}
    resp(s, "transfer_to_agent")["properties"] = {"success": {"type": "boolean"}, "message": {"type": "string"}}
    resp(s, "schedule_callback")["properties"] = {"callback_id": {"type": "string"}}
    return s


def record(wf, data, status_code=200):
    vars = {"cur_pid": "p1", "cur_amount": 300, "cur_date": "2026-10-16"}
    le.run_post(le._tool(wf, "write_promise", "create_promise"), vars, data, status_code)
    return vars


def test_success_without_an_ok_field_is_http_2xx_without_an_error():
    api, roles, _, wf = compiled(flagless_spec())
    assert "ok" not in roles["responses"]["create_promise"] and roles["responses"]["transfer_to_agent"]["ok"] == "success"
    assert "pr__p1" in record(wf, {"promise_id": "x1"})
    assert record(wf, {"promise_id": "x1"}, 500).get("oc__p1") == "service_error"
    assert record(wf, {"error": "duplicate"}).get("oc__p1") == "service_error"
    assert record(wf, {"ok": False}).get("oc__p1") == "service_error"
    assert record(wf, {"success": False}).get("oc__p1") == "service_error"
    tr = le._tool(wf, "negotiate", "transfer_to_agent")
    vars = {}
    le.run_post(tr, vars, {"success": True})
    assert vars.get("transfer_done") == "yes"
    vars = {}
    le.run_post(tr, vars, {"success": False})
    assert vars.get("transfer_done") is None and vars["tr__"] == ""
    cb = le._tool(wf, "someone_else", "schedule_callback")
    vars = {"st_json": "{}"}
    le.run_post(cb, vars, {"callback_id": "cb1"})
    assert vars.get("cb_done") == "yes"
    vars = {"st_json": "{}"}
    le.run_post(cb, vars, {}, 400)
    assert vars.get("cb_done") is None
    # the smoke run answers in the API's own shape, so the whole recording path still works
    prods = [{"product_id": "p1", "type": "personal_loan", "past_due": 500.0, "days_late": 20, "instalment_amount": 120.0}]
    assert promise_on_deadline(wf, roles, prods)[1]["record_summary"].startswith("Your promise is recorded")


def test_an_api_with_a_success_flag_still_requires_it():
    _, _, _, wf = compiled(copy.deepcopy(KIT))
    assert record(wf, {"promise_id": "x1"}).get("oc__p1") == "service_error"   # kit: no ok=true, no promise claimed
    assert "pr__p1" in record(wf, {"ok": True, "promise_id": "x1"})


# ------------------------------------------------------------------ required request fields without a role
def body_of(tool):
    return tool["function"]["parameters"]["properties"]["body"]


def test_required_fields_without_a_role_are_filled_or_left_to_the_model():
    s = copy.deepcopy(KIT)
    req(s, "add_note")["required"].append("text")
    cc = req(s, "close_call")
    cc["properties"]["summary"] = {"type": "string"}
    cc["properties"]["attempt_count"] = {"type": "integer"}
    cc["required"] += ["summary", "attempt_count"]
    vi = req(s, "verify_identity")
    vi["properties"]["channel_note"] = {"type": "string", "description": "how the answer was given"}
    vi["required"].append("channel_note")
    warnings = []
    api, roles, problems, wf = compiled(s, warnings=warnings)
    assert problems == [] and v.lint(wf, api) == []
    note = body_of(wf["nodes"]["write_note"]["pre_actions"][0])
    assert note["properties"]["text"]["default"] == cw.FILLER_TEXT and "text" in note["required"]
    close = body_of(wf["tools"][0])
    assert close["properties"]["summary"]["default"] == cw.FILLER_TEXT and close["properties"]["attempt_count"]["default"] == 0
    verify = body_of(le._tool(wf, "verify", "verify_identity"))
    assert verify["properties"]["channel_note"]["default"] == "{{channel_note}}" and "channel_note" in verify["required"]
    assert any("add_note.text" in w for w in warnings) and any("verify_identity.channel_note" in w and "model" in w for w in warnings)


def test_required_enum_without_a_role_is_a_compile_warning():
    s = copy.deepcopy(KIT)
    cp = req(s, "create_promise")
    cp["properties"]["channel"] = {"type": "string", "enum": ["phone", "web"]}
    cp["required"].append("channel")
    warnings = []
    api, _, _, wf = compiled(s, warnings=warnings)
    assert any("create_promise.channel" in w and "enum" in w for w in warnings)
    assert any("channel" in p for p in v.lint(wf, api))   # the lint still refuses to save it


def test_kit_compile_has_no_extra_field_warnings():
    warnings = []
    compiled(copy.deepcopy(KIT), warnings=warnings)
    assert warnings == []


# ------------------------------------------------------------------ R5 / R6 (written independently of this code)
VARIANTS = {
    "r5": {"verify_answer": "answer_text", "close_outcome": "result_code", "close_reason": "reason_code", "products_key": "accounts",
           "product_fields": {"product_id": "account_ref", "product_type": "product_code", "past_due": "amount_overdue", "days_late": "days_past_due"},
           "verify": {"verified": "passed", "wrong_answers_left": "attempts_left", "all_verified": "security_complete", "hint": "hint"},
           "promise": {"customer_id", "account_ref", "promised_amount", "due_date"}},
    "r6": {"verify_answer": "given_answer", "close_outcome": "disposition", "close_reason": "reason_code", "products_key": "agreements",
           "product_fields": {"product_id": "agreement_id", "product_type": "agreement_type", "past_due": "arrears", "days_late": "days_in_arrears"},
           "verify": {"verified": "correct", "wrong_answers_left": "attempts_left", "all_verified": "identity_confirmed", "hint": "hint"},
           "promise": {"customer_id", "agreement_id", "promised_amount", "due_date"}},
}


def variant(r):
    api = ot.load(str(ROOT / f"tests/variants/api-{r}.json"))
    roles, problems = ot.infer_roles(api)
    ir = {k: x for k, x in json.loads((ROOT / f"tests/variants/ir-{r}.ref.json").read_text()).items() if not k.startswith("_")}
    return api, roles, problems, ir


@pytest.mark.parametrize("r", sorted(VARIANTS))
def test_variant_roles(r):
    api, roles, problems, ir = variant(r)
    want = VARIANTS[r]
    assert problems == []
    for k in ("verify_answer", "close_outcome", "close_reason", "products_key", "product_fields"):
        assert roles[k] == want[k], k
    got = roles["responses"]["verify_identity"]
    assert {k: got.get(k) for k in want["verify"]} == want["verify"]
    assert ps.check(ir, api, roles, (ROOT / f"tests/variants/brief-{r}.md").read_text()) == []


@pytest.mark.parametrize("r", sorted(VARIANTS))
def test_variant_compiles_records_and_verifies_with_its_own_names(r):
    api, roles, _, ir = variant(r)
    warnings = []
    wf = cw.compile_workflow(ir, api, roles, warnings=warnings)
    assert v.lint(wf, api) == [] and warnings == []
    now_var = ir["call_variables"]["now"]
    prods = synthetic_products(api, roles, ir)
    pid, rep = promise_on_deadline(wf, roles, prods, now_var=now_var)
    assert set(rep["promise_sent"]) == VARIANTS[r]["promise"] and pid in rep["promise_sent"].values()
    assert rep["close_body"][roles["close_outcome"]] == ir["outcomes"]["promise"]
    names = roles["responses"]["verify_identity"]
    q = ir["identity"]["questions"]
    tool = le._tool(wf, "verify", "verify_identity")
    vars = {}
    answer = {"date": "1985-05-03", "upper_nospace": "AB12CD", "digits3": "123"}
    for i, question in enumerate(q):
        le.run_pre(tool, vars, {"customer_id": "c", roles["verify_field"]: question["api_value"], roles["verify_answer"]: answer[question["format"]]})
        le.run_post(tool, vars, {"ok": True, names["verified"]: True, names["wrong_answers_left"]: 3, names["all_verified"]: i == len(q) - 1})
        assert vars["id_state"] == ("all" if i == len(q) - 1 else "next")
    le.run_pre(tool, vars, {"customer_id": "c", roles["verify_field"]: q[0]["api_value"], roles["verify_answer"]: "1985-03-05"})
    le.run_post(tool, vars, {"ok": True, names["verified"]: False, names["wrong_answers_left"]: 3, names["hint"]: ir["identity"]["swapped_hint_value"]})
    assert vars["id_state"] == "swapped"
