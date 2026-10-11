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
