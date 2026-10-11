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
