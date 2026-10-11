"""Print a Freya call as a readable timeline: turns, node changes, tool calls/results, routing, errors.

    .venv/bin/python devtools/fetch_call.py <call_id> [--vars] [--routes] [--raw out.json]

--vars    print the last variable table seen in the call
--routes  print every route_evaluated event (default: only matched ones)
--raw     save the full call JSON (redacted) to a file
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit"))
sys.path.insert(0, str(ROOT / "devtools"))
import freya  # noqa: E402
from redact import redact  # noqa: E402


def short(x, n=300):
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + "..."


def timeline(call, show_vars=False, all_routes=False):
    lines, last_vars = [], None
    for m in call.get("messages", []):
        t = m.get("secondsFromStart")
        ts = f"{t:6.1f}s" if isinstance(t, (int, float)) else "   ?  "
        role, msg = m.get("role"), m.get("message")
        node = m.get("nodeId") or ""
        snap = m.get("workflowSnapshot") or {}
        if snap.get("variableTable"):
            last_vars = snap["variableTable"]
        if role in ("assistant", "user", "bot"):
            lines.append(f"{ts} {'AGENT' if role != 'user' else 'USER '} [{node}] {short(msg, 600)}")
            continue
        try:
            ev = json.loads(msg) if isinstance(msg, str) else (msg or {})
        except ValueError:
            lines.append(f"{ts} {role}: {short(msg)}")
            continue
        kind = ev.get("event") or ev.get("type")
        if kind == "node_enter":
            lines.append(f"{ts}   -> node {ev.get('node')} (from {ev.get('from_node')}, {short(ev.get('reason'), 120)})")
        elif kind == "function_call_start":
            args = ev.get("args") or {}
            body = args.get("body", args) if isinstance(args, dict) else args
            lines.append(f"{ts}   CALL {ev.get('function')} {short(body, 400)}")
        elif kind == "function_call_complete":
            res = ev.get("result")
            if isinstance(res, dict) and "data" in res:
                res = {"status_code": res.get("status_code"), "data": res.get("data")}
            lines.append(f"{ts}   RESULT {ev.get('function')} {short(res, 500)}")
        elif kind == "route_evaluated":
            if ev.get("variable_table"):
                last_vars = ev["variable_table"]
            if ev.get("matched") or all_routes:
                lines.append(f"{ts}   route {ev.get('from_node')} -> {ev.get('target')} matched={ev.get('matched')} {short(ev.get('reason'), 160)}")
        elif kind == "error":
            lines.append(f"{ts}   ERROR {short(ev.get('message'), 200)} {short(ev.get('details'), 300)}")
        else:
            lines.append(f"{ts}   {role}:{kind} {short(ev, 300)}")
    out = "\n".join(lines)
    out += f"\n\nendedReason: {call.get('endedReason')}  duration: {call.get('duration')}"
    if call.get("successCriteriaResult"):
        out += f"\njudge: {short(call['successCriteriaResult'], 600)}"
    if show_vars and last_vars:
        out += "\n\nlast variable table:\n" + "\n".join(
            f"  {k} = {short(v.get('value') if isinstance(v, dict) else v, 200)}" for k, v in sorted(last_vars.items()))
    return redact(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("call_id")
    ap.add_argument("--vars", action="store_true")
    ap.add_argument("--routes", action="store_true")
    ap.add_argument("--raw")
    a = ap.parse_args()
    call = freya.get_call(a.call_id)
    if a.raw:
        pathlib.Path(a.raw).write_text(redact(json.dumps(call, indent=1, ensure_ascii=False)))
    print(timeline(call, a.vars, a.routes))
