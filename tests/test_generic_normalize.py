"""General path: model-filled api_call body fields get a {{field}} default (kit docs rule), avoiding existing variable names."""
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder import generic


def test_model_filled_fields_get_a_template_default_without_name_clashes():
    wf = {"nodes": {"a": {"node_type": "code", "code": "vars['pick'] = ''"},
                    "b": {"node_type": "conversation", "tools": [{"type": "function", "function": {"name": "decide", "handler": "__function__:api_call",
                          "parameters": {"type": "object", "properties": {"body": {"type": "object", "properties": {
                              "week": {"type": "integer", "description": "chosen week"}, "pick": {"type": "integer"},
                              "card": {"type": "string", "default": "{{sel}}"}}}}}}}]}}}
    changes = generic.normalize_model_fields(wf)
    body = wf["nodes"]["b"]["tools"][0]["function"]["parameters"]["properties"]["body"]["properties"]
    assert body["week"]["default"] == "{{week}}" and body["card"]["default"] == "{{sel}}"
    assert body["pick"]["default"] == "{{decide_pick}}"   # 'pick' is a variable the workflow sets: never bake it
    assert len(changes) == 2
