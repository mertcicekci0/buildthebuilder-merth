"""Lab v2: interaction mechanics for model-called tools (results in probes/README.md).

Q-H2 code_exec tool: arguments via `params`? can it hand a result back to the model?
Q-K  model-called api tool whose pre-hook RAISES with a message: what does the model see and say? do pre-hook vars writes persist?
Q-L  a transition on a variable written by a code tool re-renders a node prompt showing that variable.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lab_v1 import S, api_tool  # noqa: E402

CHECK = """try:
    p = params if isinstance(params, dict) else {}
except NameError:
    p = {}
try:
    d = str(p.get('date_iso') or vars.get('check_date_args.date_iso') or '')
    vars['verdict'] = 'VERDICT for ' + d + ': ' + ('holiday, offer Tuesday 27 October' if d == '2026-10-26' else 'ok')
    vars['params_seen'] = 'yes' if p else 'no'
    vars['verdict_ready'] = 'yes'
    result = {'verdict': vars['verdict']}
    output = vars['verdict']
except Exception as e:
    vars['verdict'] = 'error ' + str(e)
    vars['verdict_ready'] = 'yes'
"""

STAGE = """vars['staged_notes'] = str(vars.get('staged_notes') or '') + '[p1:' + str((config.get('body') or {}).get('reason')) + ']'
raise Exception('NOTE_KEPT: the note is kept and will be saved at the end of the call. Do not mention notes. Continue with the customer.')
"""


def build():
    talk_task = ("You are a test harness. When the user asks to check a date, CALL check_date with date_iso. "
                 "When the user asks to stage a note, CALL add_note with product_id p1 and reason refused. "
                 "After every tool result, repeat to the user exactly what the tool result said, word for word.")
    nodes = {
        "talk": {"name": "talk", "node_type": "conversation", "on_enter": {"type": "tts", "text": "Lab two ready."},
                 "on_enter_first_entry_only": True, "task_message": talk_task,
                 "tools": [
                     {"type": "function", "function": {"name": "check_date", "handler": "__function__:code_exec",
                                                       "description": "CALL it when the user asks to check a date.",
                                                       "parameters": {"type": "object", "properties": {
                                                           "code": {"type": "string", "default": CHECK},
                                                           "date_iso": {"type": "string", "description": "Date as YYYY-MM-DD"}}, "required": ["date_iso"]}}},
                     api_tool("add_note", {"customer_id": S("{{customer_id}}"),
                                           "product_id": {"type": "string", "description": "product id", "default": "{{product_id}}"},
                                           "reason": {"type": "string", "enum": ["refused", "hardship", "dispute", "paid", "service_error"], "default": "{{reason}}"}},
                              required=["product_id", "reason"], pre=STAGE, description="CALL it when the user asks to stage a note.")],
                 "max_node_turns": 20, "max_turn_transition": "done",
                 "transitions": [
                     {"target_node": "show", "description": "a verdict is ready", "condition": {"logic": "or", "conditions": [
                         {"variable": "verdict_ready", "operator": "equals", "operand": "yes"}]}},
                     {"target_node": "done", "description": "The user asks to finish", "is_tool_call": True,
                      "condition": {"operator": "matches_intent", "description": "The user asks to finish"}}]},
        "show": {"name": "show", "node_type": "code", "passthrough": True, "passthrough_default_transition": "talk2", "on_enter": {"type": "silent"},
                 "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec", "parameters": {"type": "object", "properties": {
                     "code": {"type": "string", "default": "vars['verdict_ready'] = 'shown'\n"}}}}}],
                 "transitions": [{"target_node": "talk2", "description": "always", "condition": {"operator": "always"}}]},
        "talk2": {"name": "talk2", "node_type": "conversation", "on_enter": {"type": "llm"},
                  "task_message": "Tell the user, word for word: {{verdict}} (params_seen={{params_seen}}). " + talk_task,
                  "tools": [],
                  "transitions": [{"target_node": "talk", "description": "always", "condition": {"operator": "always"}}]},
        "done": {"name": "done", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "transitions": [],
                 "terminal": {"type": "function", "function": {"name": "lab2_finished", "handler": "__function__:end_call",
                                                                "parameters": {"type": "object", "properties": {"message_mode": S("custom"), "spoken_message": S("Lab two finished.")}}}}},
    }
    return {"initial_node": "talk", "system_prompt": "Engineering test workflow. Speak English.", "nodes": nodes, "global_nodes": {},
            "test_variables": {"customer_id": "c_lab", "now": "2026-10-14T11:00"}}


if __name__ == "__main__":
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "out/lab_v2.workflow.json")
    out.write_text(json.dumps(build(), indent=1))
    print(out)
