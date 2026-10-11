"""C14: fixes for the independent review of C10-C13 (role inference on renamed APIs, guard keys, prompt definitions)."""
import copy, json, pathlib, sys
import pytest
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "builder"))
from builder import openapi_tools as ot, compile_workflow as cw, extract
from test_api_robustness import KIT, IR, req, item, rename


def test_outcome_enum_beats_a_call_status_enum():
    s = copy.deepcopy(KIT)
    rename(req(s, "close_call"), "outcome", "disposition")
    req(s, "close_call")["properties"]["status"] = {"type": "string", "enum": ["completed", "dropped"]}
    roles, problems = ot.infer_roles(ot.load(s))
    assert roles["close_outcome"] == "disposition" and problems == []


def test_outcome_fallback_picks_the_enum_with_outcome_values():
    s = copy.deepcopy(KIT)
    rename(req(s, "close_call"), "outcome", "code_a")
    req(s, "close_call")["properties"]["code_b"] = {"type": "string", "enum": ["completed", "dropped"]}
    roles, _ = ot.infer_roles(ot.load(s))
    assert roles["close_outcome"] == "code_a"


def test_reason_fallback_never_takes_the_outcome_enum():
    s = copy.deepcopy(KIT)
    rename(req(s, "close_call"), "outcome", "disposition")
    rename(req(s, "close_call"), "reason", "note")
    req(s, "close_call")["properties"]["final_state"] = {"type": "string", "enum": list(KIT["paths"]["/close_call"]["post"]["requestBody"]
                                                              ["content"]["application/json"]["schema"]["properties"]["outcome"]["enum"])}
    roles, _ = ot.infer_roles(ot.load(s))
    assert roles.get("close_reason") != "final_state"


def test_product_id_shared_with_create_promise_beats_another_id_name():
    s = copy.deepcopy(KIT)
    rename(item(s), "product_id", "loan_ref")
    item(s)["properties"]["account_id"] = {"type": "string"}
    for t in ("create_promise", "add_note"):
        rename(req(s, t), "product_id", "loan_ref")
    roles, _ = ot.infer_roles(ot.load(s))
    assert roles["product_fields"]["product_id"] == "loan_ref"
    assert roles["promise_product"] == roles["note_product"] == "loan_ref"


def test_product_type_fallback_skips_a_status_enum():
    s = copy.deepcopy(KIT)
    del item(s)["properties"]["type"]
    item(s)["properties"]["status"] = {"type": "string", "enum": ["open", "closed"]}
    roles, _ = ot.infer_roles(ot.load(s))
    assert roles["product_fields"].get("product_type") != "status"


def test_product_type_named_product_is_found():
    s = copy.deepcopy(KIT)
    rename(item(s), "type", "product")
    roles, _ = ot.infer_roles(ot.load(s))
    assert roles["product_fields"]["product_type"] == "product"


def test_no_type_field_with_several_product_types_is_a_compile_error():
    s = copy.deepcopy(KIT)
    del item(s)["properties"]["type"]
    api = ot.load(s)
    roles, _ = ot.infer_roles(api)
    assert len(IR["product_types"]) > 1
    with pytest.raises(cw.CompileError, match="type field"):
        cw.compile_workflow(copy.deepcopy(IR), api, roles, warnings=[])


def test_no_type_field_with_one_product_type_applies_it_to_every_product():
    s = copy.deepcopy(KIT)
    del item(s)["properties"]["type"]
    api = ot.load(s)
    roles, _ = ot.infer_roles(api)
    ir = copy.deepcopy(IR)
    ir["product_types"] = ir["product_types"][:1]
    cfg = cw.runtime_config(ir, api, roles)
    assert cfg["product_types"]["*"] == cfg["product_types"][ir["product_types"][0]["api_type"]]


def test_partial_plan_key_ignores_item_order_and_bare_tens_blocks_once():
    api = ot.load(str(ROOT / "kit/tools/openapi.json"))
    roles, _ = ot.infer_roles(api)
    text = json.dumps(cw.compile_workflow(copy.deepcopy(IR), api, roles, warnings=[]))
    assert "sig = json.dumps(sorted(items))" in text
    assert "vars['tens_warned'] = words.strip().lower()" in text
    assert "with no new read-back" in text


def test_guide_defines_the_count_fields():
    for phrase in ("opening.max_asks:", "identity.refusals_before_close:", "alternative_asks (how many times"):
        assert phrase in extract.GUIDE


def test_lint_flags_transitions_the_engine_would_merge_and_previous_outside_globals():
    from builder import validate as v
    api = ot.load(str(ROOT / "kit/tools/openapi.json"))
    roles, _ = ot.infer_roles(api)
    wf = cw.compile_workflow(copy.deepcopy(IR), api, roles, warnings=[])
    assert v.lint(wf, api) == []
    node = next(n for n in wf["nodes"].values() if len(n.get("transitions", [])) >= 2)
    bad = copy.deepcopy(wf)
    trs = next(n for n in bad["nodes"].values() if n["name"] == node["name"])["transitions"]
    trs[1]["target_node"] = trs[0]["target_node"]
    assert any("merged into one" in p for p in v.lint(bad, api))
    bad = copy.deepcopy(wf)
    next(n for n in bad["nodes"].values() if n["name"] == node["name"])["transitions"][0]["target_node"] = "__previous__"
    assert any("unknown node __previous__" in p for p in v.lint(bad, api))


def test_build_stops_before_model_calls_without_a_freya_key(tmp_path):
    import os, subprocess
    env = {k: x for k, x in os.environ.items() if k != "FREYA_API_KEY"}
    env["BUILDER_TRACE_DIR"] = str(tmp_path)
    r = subprocess.run([sys.executable, str(ROOT / "builder/build.py"), str(ROOT / "kit/briefs/brief-a.md"),
                        str(ROOT / "kit/tools/openapi.json"), "--slot", "nokey-test"], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "FREYA_API_KEY is not set" in (r.stderr + r.stdout)
    rows = [json.loads(x) for x in (tmp_path / "nokey-test.jsonl").read_text().splitlines()]
    assert not any(x.get("kind") == "model_call" for x in rows)
    assert any(x.get("agent_llm") for x in rows)
