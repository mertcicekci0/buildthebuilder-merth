"""General path: tool-call exits to an end node from a node with api_call tools carry the ordering rule (confirmed action first)."""
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder import generic


def test_exits_after_confirmed_actions_get_the_ordering_rule_once():
    wf = {"nodes": {
        "main": {"node_type": "conversation", "tools": [{"function": {"name": "change", "handler": "__function__:api_call"}}],
                 "transitions": [{"target_node": "bye", "is_tool_call": True, "description": "Caller is done",
                                  "condition": {"operator": "matches_intent", "description": "Caller is done"}},
                                 {"target_node": "next", "condition": {"variable": "x", "operator": "equals", "operand": "1"}}]},
        "talk": {"node_type": "conversation", "tools": [], "transitions": [{"target_node": "bye", "is_tool_call": True, "description": "done"}]},
        "next": {"node_type": "conversation"}, "bye": {"node_type": "end_call"}}}
    assert generic.guard_exits(wf) == ["main -> bye"]
    tr = wf["nodes"]["main"]["transitions"][0]
    assert generic.ORDER_RULE in tr["description"] and generic.ORDER_RULE in tr["condition"]["description"]
    assert wf["nodes"]["talk"]["transitions"][0]["description"] == "done"          # no actions in that node: unchanged
    assert generic.guard_exits(wf) == []                                            # idempotent
