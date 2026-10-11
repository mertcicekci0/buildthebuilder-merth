"""Generic policy slots: counts, sentences, bands, queues, holidays, weekday words and reasons come from the IR."""
import copy, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from builder import openapi_tools as ot, compile_workflow as cw, validate as v

KIT = json.load(open(ROOT / "kit/tools/openapi.json"))
IR = {k: x for k, x in json.load(open(ROOT / "tests/fixtures/ir_public_ab.json")).items() if not k.startswith("_")}


def build(ir, spec=KIT, warnings=None):
    api = ot.load(spec); roles, _ = ot.infer_roles(api)
    wf = cw.compile_workflow(ir, api, roles, warnings=warnings)
    assert v.lint(wf, api) == [], v.lint(wf, api)
    return api, roles, wf


def _texts(wf):
    return json.dumps(wf)


def test_counts_come_from_the_ir():
    ir = copy.deepcopy(IR)
    ir["opening"]["max_asks"] = 3
    ir["identity"]["refusals_before_close"] = 3
    ir["callback"]["alternative_asks"] = 2
    _, _, wf = build(ir)
    op, ver, se = wf["nodes"]["opening"], wf["nodes"]["verify"], wf["nodes"]["someone_else"]
    assert "still unclear after the third ask." in op["task_message"] and "close_unclear: still unclear after the second ask" not in op["task_message"]
    assert any(t["description"].endswith("after the third ask") for t in op["transitions"])
    assert "when they have refused 3 times (the third refusal)" in ver["task_message"]
    assert "a second time" not in ver["task_message"]
    assert any(t["description"] == "The customer declined the security questions 3 times" for t in ver["transitions"])
    assert "no acceptable time after 2 re-asks" in se["task_message"]
    pre = se["tools"][0]["pre_process_code"]
    assert "at most 2 times in total" in pre and "the second such ask" in pre and "Ask once for another time" not in pre


def test_one_refusal_and_no_alternative_ask():
    ir = copy.deepcopy(IR)
    ir["opening"]["max_asks"] = 1
    ir["identity"]["refusals_before_close"] = 1
    ir["callback"]["alternative_asks"] = 0
    _, _, wf = build(ir)
    assert "still unclear after the first ask." in wf["nodes"]["opening"]["task_message"]
    ver = wf["nodes"]["verify"]["task_message"]
    assert "If the customer refuses to answer: say \"These questions protect your account.\" and call transition_to_close_declined." in ver
    assert "If they refuse again" not in ver
    se = wf["nodes"]["someone_else"]
    assert "Do not ask for another time" in se["tools"][0]["pre_process_code"]
    assert "do not ask for another" in se["task_message"]


def test_after_verification_sentence_slot():
    from builder import policy_schema as ps
    s = ps.schema()
    assert "after_verification_sentence" in s["required"] and s["properties"]["after_verification_sentence"] == {"type": "string"}
    _, _, wf = build(IR)
    assert wf["nodes"]["negotiate"]["on_enter"]["text"] == "Thank you, you are verified. {{debt_statement}} {{first_ask}}"
    ir = copy.deepcopy(IR)
    ir["after_verification_sentence"] = " This is an attempt to collect a debt, <first_name>. "
    _, _, wf = build(ir)
    assert wf["nodes"]["negotiate"]["on_enter"]["text"] == \
        "Thank you, you are verified. This is an attempt to collect a debt, {{first_name}}. {{debt_statement}} {{first_ask}}"
    legacy = {k: x for k, x in IR.items() if k != "after_verification_sentence"}   # an older IR without the field still compiles
    assert build(legacy)[2]["nodes"]["negotiate"]["on_enter"]["text"].startswith("Thank you, you are verified. {{debt_statement}}")


def test_bands_out_of_range_still_get_a_deadline():
    import datetime as dt
    from builder import runtime_src as rt
    cfg = json.load(open(ROOT / "tests/fixtures/runtime_cfg_public_ab.json"))   # early 1-7, late 8-45
    today = dt.date(2026, 10, 14)
    assert rt.band_for(0, cfg)["name"] == "early" and rt.band_for(-3, cfg)["name"] == "early"
    assert rt.band_for(46, cfg)["name"] == "late" and rt.band_for(400, cfg)["name"] == "late"
    assert rt.band_for(7, cfg)["name"] == "early" and rt.band_for(8, cfg)["name"] == "late"
    assert rt.band_for("x", cfg) is None and rt.band_for(5, {"bands": []}) is None
    assert rt.deadline_for(today, 400, cfg) == rt.deadline_for(today, 14, cfg)
    assert rt.deadline_for(today, 0, cfg) == rt.deadline_for(today, 5, cfg)
    gap = {"bands": [{"name": "a", "min": 1, "max": 5}, {"name": "b", "min": 10, "max": 20}]}
    assert rt.band_for(7, gap)["name"] == "a"   # in a gap: the band below


def test_band_checks_flag_inverted_gaps_and_overlaps():
    from builder import policy_schema as ps
    api = ot.load(KIT); roles, _ = ot.infer_roles(api)
    assert not [p for p in ps.check(IR, api, roles) if "band" in p]
    for bands, word in [([("a", 1, 7), ("b", 8, 3)], "below min"), ([("a", 1, 7), ("b", 9, 45)], "gap or overlap"),
                        ([("a", 1, 7), ("b", 6, 45)], "gap or overlap")]:
        ir = copy.deepcopy(IR)
        ir["bands"] = [dict(IR["bands"][0], name=n, min=lo, max=hi) for n, lo, hi in bands]
        ir["ladder"] = []
        assert any(word in p for p in ps.check(ir, api, roles)), (bands, ps.check(ir, api, roles))


def test_empty_or_unknown_transfer_queue_drops_only_that_path():
    ir = copy.deepcopy(IR)
    ir["transfer_queues"]["human"] = ""
    w = []
    _, _, wf = build(ir, warnings=w)
    assert any("transfer queue human" in x for x in w), w
    assert "transfer_human" not in wf["nodes"]
    assert "transition_to_transfer_human" not in _texts(wf)
    for n in wf["nodes"].values():
        assert all(t["target_node"] in wf["nodes"] for t in n.get("transitions", []))
    desc = [t for t in wf["nodes"]["negotiate"]["tools"] if t["function"]["name"] == "transfer_to_agent"][0]["function"]["description"]
    assert desc == "Hand the call to a person. CALL it for: restructuring request (queue restructuring), customer accepted the support team after hardship (queue support)."

    ir = copy.deepcopy(IR)
    ir["transfer_queues"]["support"] = "no_such_queue"
    w = []
    _, _, wf = build(ir, warnings=w)
    assert any("transfer queue support" in x and "not in the API queues" in x for x in w), w
    assert "transfer_human" in wf["nodes"] and "no_such_queue" not in _texts(wf)

    ir = copy.deepcopy(IR)
    ir["transfer_queues"] = {"restructuring": "", "human": "", "support": "x"}
    w = []
    _, _, wf = build(ir, warnings=w)
    assert len([x for x in w if x.startswith("transfer queue")]) == 3
    assert not any(t["function"]["name"] == "transfer_to_agent" for n in wf["nodes"].values() for t in n.get("tools") or [])
    assert "transfer_human" not in wf["nodes"]


def test_holiday_mentions_in_many_written_forms():
    from builder import policy_schema as ps
    for text in ["25 Dec", "Dec 25", "Dec. 25", "25th December", "December 25th", "the 25th of December", "24–26 December", "24-26 Dec",
                 "24 to 26 December", "December 24-26", "Friday 25 December", "12/25/2026", "25-12-2026", "25.12.2026", "25/12", "25.12.",
                 "**25 December**", "2026-12-25", "December 25, 2026"]:
        assert ps._date_mentioned("2026-12-25", "Closed on " + text + " for the holiday."), text
    for text in ["26 to 28 December", "December 2026", "1-7 days late", "12/24/2026", "25.12.2025", "Dec 2", "125 December", "1.5 hours"]:
        assert not ps._date_mentioned("2026-12-25", "Closed on " + text + "."), text
    assert ps._date_mentioned("2026-05-01", "1 May") and not ps._date_mentioned("2026-05-01", "1.5 times")
    assert ps._date_mentioned("2026-09-14", "Sept 14")


def test_unmentioned_holiday_is_a_warning_not_a_problem():
    from builder import policy_schema as ps
    api = ot.load(KIT); roles, _ = ot.infer_roles(api)
    ir = copy.deepcopy(IR); ir["evidence"] = []
    ir["calendar"]["holidays"] = ["2026-12-25", "2026-11-11"]
    brief = "Branches are closed on Christmas Day (Dec 25)."
    w = []
    assert ps.check(ir, api, roles, brief, warnings=w) == []
    assert w == ["holiday 2026-11-11 was not found written in the brief (kept; check it)"]
    assert ps.check(ir, api, roles, brief) == []   # old call form: never a problem


def test_next_weekday_meaning_follows_the_ir():
    import datetime as dt
    from builder import policy_schema as ps, runtime_src as rt
    assert ps.schema()["properties"]["calendar"]["properties"]["next_weekday"]["enum"] == ["following_week", "next_occurrence"]
    cfg = json.load(open(ROOT / "tests/fixtures/runtime_cfg_public_ab.json"))
    D = dt.date.fromisoformat
    wed, fri, sun = D("2026-10-14"), D("2026-10-16"), D("2026-10-18")
    for mode in (None, "following_week"):
        c = copy.deepcopy(cfg)
        if mode:
            c["calendar"]["next_weekday"] = mode
        assert rt.resolve_day_words("next Friday", wed, c) == (D("2026-10-23"), "next_weekday")
        assert rt.resolve_day_words("next Friday", fri, c) == (D("2026-10-23"), "next_weekday")
    c = copy.deepcopy(cfg); c["calendar"]["next_weekday"] = "next_occurrence"
    assert rt.resolve_day_words("next Friday", wed, c) == (D("2026-10-16"), "next_weekday")
    assert rt.resolve_day_words("next Friday", fri, c) == (D("2026-10-23"), "next_weekday")    # never today
    assert rt.resolve_day_words("next Monday", sun, c) == (D("2026-10-19"), "next_weekday")
    assert rt.resolve_day_words("this Friday", wed, c) == (D("2026-10-16"), "this_weekday")
    assert "next friday = Friday 16 October" in rt.relative_dates_text(wed, c)
    assert "next friday = Friday 23 October" in rt.relative_dates_text(wed, cfg)
    ir = copy.deepcopy(IR); ir["calendar"]["next_weekday"] = "next_occurrence"
    _, _, wf = build(ir)
    assert '"next_weekday":"next_occurrence"' in wf["nodes"]["init"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def test_paid_reason_sent_when_the_reason_enum_has_it():
    from builder import local_exec as le
    st = {"products": {}, "promises": {}, "notes": {"p1": "paid"}, "order": ["p1"], "transfer": "", "verified": "yes", "contact": "customer"}

    def close(spec, ir):
        _, _, wf = build(ir, spec)
        return le.run_pre(wf["tools"][0], {"st_json": json.dumps(st)}, {"customer_id": "c1", "outcome": "", "reason": ""})
    ir = copy.deepcopy(IR); ir["outcomes"]["reasons"]["paid"] = "P2"
    assert close(KIT, ir) == {"customer_id": "c1", "outcome": "paid"}            # free-text reason: a distinct paid outcome sends none
    spec = copy.deepcopy(KIT)
    sch = spec["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    sch["properties"]["reason"]["enum"] = sorted(set(IR["outcomes"]["reasons"].values()) - {""}) + ["P2"]
    assert close(spec, ir) == {"customer_id": "c1", "outcome": "paid", "reason": "P2"}
    ir["outcomes"]["reasons"]["paid"] = "not_a_code"                              # not in the enum: still not sent
    assert close(spec, ir) == {"customer_id": "c1", "outcome": "paid"}


def test_llm_entered_steps_always_speak_first():
    _, _, wf = build(IR)
    llm_nodes = [n for n in wf["nodes"].values() if (n.get("on_enter") or {}).get("type") == "llm"]
    assert [n["name"] for n in llm_nodes] == ["someone_else"]
    lines = wf["nodes"]["someone_else"]["task_message"].split("\n")
    assert lines[1] == cw.SPEAK_ON_ENTRY["someone_else"]
    assert "repeat that question in one short sentence" in lines[1]


# ---------------------------------------------------------------- kit golden: only documented changes since 4baeea6
GOLDEN = ROOT / "tests/fixtures/golden_w2_kit_workflow.json"
# prompt text: (node, old text in the golden, new text); the old text must occur exactly once
TEXT_EDITS = {
    "someone_else": [("never mention any account detail, even if they offer to pay.\n",
                      "never mention any account detail, even if they offer to pay.\n" + cw.SPEAK_ON_ENTRY["someone_else"] + "\n")],
    "verify": [
        ("call verify_identity with 1980-06-05 now, never \"Go on.\". ", "call verify_identity with 1980-06-05 now, never ask for it again. "),
        ("(also one before a \"Go on.\" or a complaint)", "(also one before a cut-off or a complaint)"),
        ("do not repeat the question; reply with two or three words such as \"Take your time.\" or \"Go on.\" and let them finish. After two such replies in a row, "
         "ask the question again in one short sentence. Right after you gave the explanation below or answered their question, a message without an answer gets "
         "the question again in one short sentence instead.\n",
         "reply at once by asking the pending question again in one short, slightly rephrased sentence (for a partial answer, ask only for the missing part, "
         "such as the year or the remaining digits); call no tool and never add the refusal explanation to this re-ask. Never invent or complete digits or a year yourself.\n")],
    "negotiate": [
        ("A message that stops mid-sentence or is only small talk is not a refusal and not a non-answer: reply with two or three words such as \"Take your time.\" "
         "or \"Go on.\", call no tool and make no new offer; after two such replies, repeat your last question. ",
         "A message with no answer yet (only small talk, or cut off mid-sentence before the answer) is not a refusal and not a non-answer: reply at once in one "
         "short sentence asking them to say it again (e.g. \"Sorry, I missed that, could you say that again?\"); never restate an offer you already made, call no tool "
         "and make no new offer. "),
        # C13: numbers given in two parts are joined before judging
        ("A day or an amount at the very end of a message that stops mid-word may be cut off",
         "A number may come in two parts over consecutive messages (\"twelve\" then \"fifty\" = 1250; \"the twenty\" then \"third\" = the 23rd): "
         "join the parts before you check or judge it, never judge one part alone; if you are not sure the parts belong together, read the joined value back and ask. "
         "A day or an amount at the very end of a message that stops mid-word may be cut off"),
        # C12: a question about an offer is answered without asking for it again; other questions do not repeat an offer
        ("\"That is the amount I mentioned, <amount>, <and every condition you said with it>. Would that work for you?\" That is an answer, not a new attempt: no tool.\n",
         "\"That is the amount I mentioned, <amount>, <and every condition you said with it>.\" Then stop: do not ask for it again in that turn. That is an answer, "
         "not a new attempt: no tool.\nWhen you answer any other question from the customer (which account, why you are calling, who you are), answer it and stop; "
         "do not repeat an offer you already made.\n")],
}
# embedded runtime: top-level definitions that may differ (band clamp, next_weekday option)
RUNTIME_CHANGED = {"band_for", "next_weekday_date", "relative_dates_text", "resolve_day_words"}
CFG_MARK = "\nCFG = json.loads("


def _split_code(src):
    import ast
    i = src.index(CFG_MARK)
    j = src.index("\n", i + 1)
    cfg = json.loads(ast.parse(src[i + 1:j]).body[0].value.args[0].value)
    defs = {}
    for node in ast.parse(src[:i]).body:
        name = getattr(node, "name", None) or ast.unparse(node)
        defs[name] = ast.unparse(node)
    return defs, cfg, src[j + 1:]


def _without_bare_tens_guard(code):
    lines = code.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("if re.search(r'\\b(twenty|thirty)"):
            j = i + 1
            while j < len(lines) and lines[j].startswith(" "):
                j += 1
            return "\n".join(lines[:i] + lines[j:])
    return code


def _without_partial_plan_guard(code):
    start = code.find("planned = [x[0] for x in items]")
    end = code.find("vars['plan_json'] = json.dumps(items)")
    return code[:start] + code[end:] if 0 <= start < end else code


def _same_code(old, new, where):
    od, ocfg, oentry = _split_code(old)
    nd, ncfg, nentry = _split_code(new)
    nentry = _without_bare_tens_guard(nentry)   # documented C10 change: check_date refuses cut-off "twenty"/"thirty" day words
    nentry = _without_partial_plan_guard(nentry)   # documented C11 change: save_promises blocks a partial plan once while products are open
    assert nentry == oentry, f"{where}: entry code changed"
    ocfg["calendar"]["next_weekday"] = "following_week"
    assert ncfg == ocfg, f"{where}: CFG changed beyond calendar.next_weekday"
    assert set(od) <= set(nd), f"{where}: runtime definitions removed {set(od) - set(nd)}"
    changed = {k for k in nd if od.get(k) != nd[k]}
    assert changed <= RUNTIME_CHANGED, f"{where}: undocumented runtime change in {changed - RUNTIME_CHANGED}"


def _compare(old, new, where, node=None):
    assert type(old) is type(new), where
    if isinstance(old, dict):
        assert set(old) == set(new), f"{where}: keys {set(old) ^ set(new)}"
        node = old.get("name", node) if "node_type" in old else node
        for k in old:
            _compare(old[k], new[k], f"{where}.{k}", node)
    elif isinstance(old, list):
        assert len(old) == len(new), where
        for i, (a, b) in enumerate(zip(old, new)):
            _compare(a, b, f"{where}[{i}]", node)
    elif isinstance(old, str) and CFG_MARK in old:
        _same_code(old, new, where)
    elif isinstance(old, str) and where.endswith(".task_message") and node in TEXT_EDITS:
        for a, b in TEXT_EDITS[node]:
            assert old.count(a) == 1, (where, a)
            old = old.replace(a, b)
        assert new == old, f"{where}: text differs beyond the documented edits"
    else:
        assert new == old, where


def test_kit_workflow_differs_from_the_golden_only_as_documented():
    _, _, wf = build(IR)
    _compare(json.load(open(GOLDEN)), json.loads(json.dumps(wf)), "wf")
