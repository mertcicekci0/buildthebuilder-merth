"""Run the generated workflow's code locally, the way Freya runs it (verified: code nodes get `vars`; hooks get
`vars` + `config` (pre) or `result` (post); code tools get `params`). Used by tests and as a build-time self-check
that needs no platform call and no model call.
"""
import copy
import datetime
import json


class Blocked(Exception):
    pass


def _node_code(wf, node):
    return wf["nodes"][node]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def _tool(wf, node, name):
    n = wf["nodes"][node]
    for t in (n.get("tools") or []) + (n.get("pre_actions") or []):
        if t["function"]["name"] == name:
            return t
    raise KeyError(f"{node}/{name}")


def run_code_node(wf, node, vars):
    exec(_node_code(wf, node), {"vars": vars})
    return vars


def run_pre(tool, vars, body):
    config = {"body": copy.deepcopy(body)}
    try:
        exec(tool["pre_process_code"], {"vars": vars, "config": config})
    except Exception as e:  # a raising pre-hook blocks the request on Freya
        raise Blocked(str(e)) from None
    return config["body"]


def run_post(tool, vars, data, status_code=200):
    result = {"status": "success", "data": copy.deepcopy(data), "status_code": status_code, "header": {}}
    exec(tool["post_process_code"], {"vars": vars, "result": result})
    return result["data"]


def run_code_tool(tool, vars, params):
    exec(tool["function"]["parameters"]["properties"]["code"]["default"], {"vars": vars, "params": params})
    return vars


def flatten_products(prefix, products):
    out = {}
    for i, p in enumerate(products):
        for k, v in p.items():
            out[f"{prefix}.{i}.{k}"] = v
    return out


def smoke(wf, roles, products, now="2026-10-14T11:00", promise=None):
    """Drive init -> account -> one guarded promise -> wrap-up -> close on synthetic data. Returns a report dict.
    `products` are dicts with the API's own field names; `promise` = (product_id, amount, date) to try."""
    vars = {"now": now, "customer_id": "c_local", "first_name": "Test", "last_name": "Customer", "title": "Mr"}
    run_code_node(wf, "init", vars)
    prefix = f"load_account_response.data.{roles['products_key']}"
    vars.update(flatten_products(prefix, products))
    run_code_node(wf, "account", vars)
    report = {"init_ok": vars.get("init_ok"), "account_ok": vars.get("account_ok"), "debt_statement": vars.get("debt_statement"),
              "first_ask": vars.get("first_ask"), "facts_text": vars.get("facts_text"), "table_lines": len(str(vars.get("date_table", "")).splitlines())}
    if promise:
        tool = _tool(wf, "negotiate", "save_promises")
        plan = "; ".join(f"{pid} {amt} {d}" for pid, amt, d in (promise if isinstance(promise, list) else [promise]))
        try:
            run_pre(tool, vars, {"plan": plan})
            report["promise_blocked"] = "pre-hook did not raise"
        except Blocked as b:
            msg = str(b)
            if not msg.startswith("ACCEPTED"):
                report["promise_blocked"] = msg
            else:
                rec = _tool(wf, "write_promise", "create_promise")
                sent = []
                for _ in range(10):
                    run_code_node(wf, "record_next", vars)
                    if vars.get("rec_go") != "yes":
                        break
                    body = {k: (vars.get(v["default"][2:-2]) if isinstance(v.get("default"), str) and v["default"].startswith("{{") else v.get("default"))
                            for k, v in rec["function"]["parameters"]["properties"]["body"]["properties"].items()}
                    sent.append(body)
                    run_post(rec, vars, {"ok": True, "promise_id": f"pr{len(sent)}"})
                report["promise_sent"] = sent[0] if len(sent) == 1 else sent
                report["record_summary"] = vars.get("record_summary")
    run_code_node(wf, "wrap_up", vars)
    report["notes_pending"] = vars.get("notes_pending")
    close = wf["tools"][0]
    report["close_body"] = run_pre(close, vars, {roles["customer_id"]: "c_local", roles["close_outcome"]: vars.get("close_outcome"), roles["close_reason"]: ""})
    report["state"] = json.loads(vars["st_json"])
    return report
