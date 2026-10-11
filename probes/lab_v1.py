"""Lab workflow v1: one chat session that answers several platform-semantics questions at once.

Run against a PUB-10 practice session (its first create_promise fails with HTTP 500), with now overridden.
Questions (see probes/README.md for the results):
  Q-A save/read-back of code, api_request, hooks, lifecycle tool, extraction, tool-call transition, code_exec tool
  Q-B code node reads call variables, writes str/list/dict; how ISO dates and `now` render in a tts line
  Q-C engine-run api_request re-entered with new baked values (re-baking on entry)
  Q-D pre-hook that drops customer_id (abort by 404) / pre-hook that raises: does the request still go out?
  Q-E post-hook on a pre_action: runs? edits visible?  Q-F lifecycle close_call from variables at chat end
  Q-G tool-call transition  Q-H code_exec tool called by the model  Q-I date extraction anchor (real clock vs now)
  Q-J config.maxRetries on a write that returns HTTP 500
"""
import json
import pathlib
import sys

BANK = "https://freya-bank-mock.freyavoice.workers.dev/v1"


def api_tool(name, body_props, required=(), max_retries=0, pre=None, post=None, description=""):
    t = {"type": "function", "config": {"timeoutMs": 12000, "maxRetries": max_retries},
         "function": {"name": name, "handler": "__function__:api_call", "description": description or name,
                      "parameters": {"type": "object", "properties": {
                          "url": {"type": "string", "default": f"{BANK}/{name}"},
                          "method": {"type": "string", "default": "POST"},
                          "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                          "body": {"type": "object", "properties": body_props, "required": list(required)}}}}}
    if pre:
        t["pre_process_code"] = pre
    if post:
        t["post_process_code"] = post
    return t


def code_node(name, code, nxt):
    return {"name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": nxt,
            "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
            "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                             "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": code},
                                                                             "timeout_ms": {"type": "number", "default": 10000}}}}}],
            "transitions": [{"target_node": nxt, "description": "always", "condition": {"operator": "always"}}]}


def api_node(name, tool, nxt, mapping=(), extra_transitions=()):
    return {"name": name, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": nxt,
            "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "pre_actions": [tool],
            "response_mapping": [{"path": p, "variable": v} for p, v in mapping],
            "transitions": list(extra_transitions) + [{"target_node": nxt, "description": "always", "condition": {"operator": "always"}}]}


S = lambda v: {"type": "string", "default": v}  # noqa: E731

INIT = """import datetime, json
try:
    raw = str(vars.get('now') or '')
    now = datetime.datetime.fromisoformat(raw[:16])
    vars['lab_now_seen'] = raw
    vars['today_iso'] = now.date().isoformat()
    vars['today_text'] = now.strftime('%A %d %B %Y')
    vars['facts'] = 'line one: today is ' + now.strftime('%A') + '\\nline two: second line'
    vars['probe_list'] = ['a', 'b']
    vars['probe_dict'] = {'k': 1}
    vars['cur_product_id'] = 'p1'
    vars['cur_amount'] = 365
    vars['cur_date'] = '2026-10-16'
    vars['write_round'] = '0'
    vars['close_outcome'] = 'promise'
    vars['close_reason'] = ''
    vars['lab_flag'] = 'ready'
except Exception as e:
    vars['lab_flag'] = 'init_error ' + str(e)
"""

POST_MARK = """try:
    vars['post_hook_ran'] = 'yes'
    if isinstance(result, dict) and isinstance(result.get('data'), dict):
        result['data']['lab_post'] = 'seen'
        result.pop('header', None)
except Exception:
    pass
"""

PREP = """import json
try:
    p = vars.get('products')
    vars['products_type'] = type(p).__name__
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception:
            pass
    vars['products_repr'] = json.dumps(p)[:300] if not isinstance(p, str) else p[:300]
    keys = sorted(k for k in vars.keys() if k.startswith('get_account'))
    vars['get_account_keys'] = ','.join(keys)[:400]
except Exception as e:
    vars['products_repr'] = 'prep_error ' + str(e)
"""

PROMISE_POST = """try:
    d = result.get('data') if isinstance(result, dict) else None
    log = str(vars.get('promise_log') or '')
    vars['promise_log'] = log + '[status=' + str(result.get('status_code') if isinstance(result, dict) else '?') + ' data=' + str(d) + ']'
except Exception as e:
    vars['promise_log'] = 'post_error ' + str(e)
"""

AFTER_WRITE = """try:
    r = int(str(vars.get('write_round') or '0')) + 1
    vars['write_round'] = str(r)
    if r == 1:
        vars['cur_amount'] = 111
        vars['cur_date'] = '2026-10-19'
        vars['loop_again'] = 'yes'
    else:
        vars['loop_again'] = 'no'
except Exception as e:
    vars['loop_again'] = 'no'
    vars['after_write_error'] = str(e)
"""

DROP_ID = """try:
    b = config.get('body')
    if isinstance(b, dict):
        b.pop('customer_id', None)
    vars['drop_hook_ran'] = 'yes'
except Exception:
    pass
"""

RAISE = "vars['raise_hook_ran'] = 'yes'\nraise Exception('lab abort')\n"

CODE_TOOL = """try:
    got = None
    try:
        got = x
        src = 'local x'
    except NameError:
        src = 'no local x'
    if got is None:
        got = vars.get('x') or vars.get('lab_code_tool_args.x')
        src += ' / from vars'
    vars['code_tool_got'] = str(got) + ' (' + src + ')'
    vars['code_tool_keys'] = ','.join(sorted(k for k in vars.keys() if 'lab_code_tool' in k or k == 'x'))[:300]
except Exception as e:
    vars['code_tool_got'] = 'error ' + str(e)
"""

CLOSE_PRE = """try:
    b = config.get('body')
    if isinstance(b, dict) and not b.get('reason'):
        b.pop('reason', None)
except Exception:
    pass
"""

REPORT_TTS = ("Lab report. now renders as {{now}}. today_iso renders as {{today_iso}}. cur_date renders as {{cur_date}}. "
              "amount renders as {{cur_amount}}. today_text is {{today_text}}.")

REPORT_TASK = """You are a test harness, not a phone agent. Answer the user literally.
Known values (repeat them exactly when asked):
lab_flag={{lab_flag}}
lab_now_seen={{lab_now_seen}}
facts={{facts}}
probe_list={{probe_list}}
probe_dict={{probe_dict}}
products_type={{products_type}}
products_repr={{products_repr}}
get_account_keys={{get_account_keys}}
post_hook_ran={{post_hook_ran}}
promise_log={{promise_log}}
write_round={{write_round}}
drop_hook_ran={{drop_hook_ran}}
raise_hook_ran={{raise_hook_ran}}
code_tool_got={{code_tool_got}}
code_tool_keys={{code_tool_keys}}
said_date={{said_date}}
When the user asks you to run the lab code tool, CALL lab_code_tool with the text they give as x. When the user asks to finish, CALL transition_to_finish. After every tool result, say one short sentence."""


def build():
    nodes = {
        "start": {"name": "start", "node_type": "conversation",
                  "on_enter": {"type": "tts", "text": "Lab ready."},
                  "task_message": "Say only: ok. Do not call any tool in this turn.",
                  "transitions": [{"target_node": "init", "description": "always", "condition": {"operator": "always"}}]},
        "init": code_node("init", INIT, "v_dob"),
        "v_dob": api_node("v_dob", api_tool("verify_identity", {"customer_id": S("{{customer_id}}"), "field": S("date_of_birth"), "answer": S("1989-02-23")},
                                            post=POST_MARK), "v_phone"),
        "v_phone": api_node("v_phone", api_tool("verify_identity", {"customer_id": S("{{customer_id}}"), "field": S("phone_last4"), "answer": S("9904")}), "acct"),
        "acct": api_node("acct", api_tool("get_account", {"customer_id": S("{{customer_id}}")}), "prep", mapping=[("data.products", "products")]),
        "prep": code_node("prep", PREP, "write_promise"),
        "write_promise": api_node("write_promise", api_tool("create_promise", {
            "customer_id": S("{{customer_id}}"), "product_id": S("{{cur_product_id}}"),
            "amount": {"type": "number", "default": "{{cur_amount}}"}, "date": S("{{cur_date}}")}, max_retries=1, post=PROMISE_POST), "after_write"),
        "after_write": {**code_node("after_write", AFTER_WRITE, "abort_drop_id"), "transitions": [
            {"target_node": "write_promise", "description": "loop once more", "condition": {"logic": "or", "conditions": [
                {"variable": "loop_again", "operator": "equals", "operand": "yes"}]}},
            {"target_node": "abort_drop_id", "description": "always", "condition": {"operator": "always"}}]},
        "abort_drop_id": api_node("abort_drop_id", api_tool("add_note", {"customer_id": S("{{customer_id}}"), "product_id": S("p1"), "reason": S("refused")},
                                                            pre=DROP_ID), "abort_raise"),
        "abort_raise": api_node("abort_raise", api_tool("schedule_callback", {"customer_id": S("{{customer_id}}"), "date": S("2026-10-15"), "time": S("11:00")},
                                                         pre=RAISE), "report"),
        "report": {"name": "report", "node_type": "conversation",
                   "on_enter": {"type": "tts", "text": REPORT_TTS}, "on_enter_first_entry_only": True,
                   "task_message": REPORT_TASK,
                   "extraction_schema": [{"name": "said_date", "type": "date", "description": "A calendar date the user mentions for a payment", "min_confidence": 0.5}],
                   "tools": [{"type": "function", "function": {"name": "lab_code_tool", "handler": "__function__:code_exec",
                                                                "description": "CALL it when the user asks to run the lab code tool. Pass the text as x.",
                                                                "parameters": {"type": "object", "properties": {
                                                                    "code": {"type": "string", "default": CODE_TOOL},
                                                                    "x": {"type": "string", "description": "The text the user gave"}}, "required": ["x"]}}}],
                   "max_node_turns": 20, "max_turn_transition": "finish",
                   "transitions": [{"target_node": "finish", "description": "The user asks to finish the lab", "is_tool_call": True,
                                    "condition": {"operator": "matches_intent", "description": "The user asks to finish the lab"}}]},
        "finish": {"name": "finish", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                   "transitions": [], "terminal": {"type": "function", "function": {"name": "lab_finished", "handler": "__function__:end_call",
                                                                                     "parameters": {"type": "object", "properties": {
                                                                                         "message_mode": S("custom"), "spoken_message": S("Lab finished. Goodbye.")}}}}},
    }
    close = api_tool("close_call", {"customer_id": S("{{customer_id}}"), "outcome": S("{{close_outcome}}"), "reason": S("{{close_reason}}")}, pre=CLOSE_PRE)
    close["config"]["alwaysRunsAt"] = ["end_of_call"]
    return {"initial_node": "start", "system_prompt": "This is an engineering test workflow. Speak English.",
            "nodes": nodes, "global_nodes": {}, "tools": [close],
            "test_variables": {"customer_id": "c_lab", "first_name": "Lab", "last_name": "Test", "title": "Mr", "now": "2026-11-20T10:00"}}


if __name__ == "__main__":
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "out/lab_v1.workflow.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build(), indent=1))
    print(out)
