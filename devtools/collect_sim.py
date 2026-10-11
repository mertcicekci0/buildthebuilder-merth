"""Score a finished simulation from the platform alone (used when a runner died before saving its plan).

    .venv/bin/python devtools/collect_sim.py <simulation_id> [--cases kit/public-test-set.json] [--label NAME]

Case id = first token of the persona name; customer_id = the customer_id found in the call's tool-call arguments.
A call that made no tool call has no ledger to read and counts as a fail.
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit"))
sys.path.insert(0, str(ROOT / "kit" / "tools"))
sys.path.insert(0, str(ROOT / "devtools"))
import freya  # noqa: E402
from score import score_ledger  # noqa: E402
import bank  # noqa: E402
from fetch_call import timeline  # noqa: E402
from redact import redact  # noqa: E402


def customer_id_of(call):
    for m in call.get("messages", []):
        if m.get("role") != "system":
            continue
        try:
            ev = json.loads(m["message"]) if isinstance(m.get("message"), str) else (m.get("message") or {})
        except ValueError:
            continue
        if ev.get("event") == "function_call_start":
            body = (ev.get("args") or {}).get("body") or {}
            if isinstance(body, dict) and body.get("customer_id"):
                return body["customer_id"]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("simulation_id")
    ap.add_argument("--cases", default=str(ROOT / "kit" / "public-test-set.json"))
    ap.add_argument("--label")
    a = ap.parse_args()
    cases = {c.get("run_id", c["id"]): c for c in json.load(open(a.cases))}
    out_dir = ROOT / "results" / (a.label or f"collect-{a.simulation_id[:8]}")
    (out_dir / "calls").mkdir(parents=True, exist_ok=True)
    its = freya.api("GET", f"/api/v2/simulations/{a.simulation_id}/iterations")
    its = its.get("iterations") or its.get("items") or its
    runs = freya.get_iteration(a.simulation_id, its[0]["id"])["runs"]
    rows = []
    for r in sorted(runs, key=lambda x: x.get("personaName", "")):
        case = cases.get((r.get("personaName") or "").split()[0])
        call = freya.get_call(r["callId"]) if r.get("callId") else {}
        cid = customer_id_of(call)
        failed = []
        led = {}
        if case is None:
            failed.append("unknown case")
        elif not cid:
            failed.append("no tool call, so no ledger")
        else:
            st, led = bank.ledger(cid)
            failed += score_ledger(case, led)["failed"] if st == 200 else [f"ledger {st}"]
        if r.get("outcome") != "pass":
            failed.append("conversation: " + " ".join(str(r.get("reason") or "no verdict").split())[:400])
        name = (r.get("personaName") or "?").split()[0]
        if call:
            (out_dir / "calls" / f"{name}.txt").write_text(timeline(call, show_vars=True))
        tools = " > ".join(c["tool"] for c in led.get("calls", [])) if isinstance(led, dict) else ""
        rows.append({"id": name, "pass": not failed, "failed": failed, "call_id": r.get("callId"), "turns": r.get("turnCount"),
                     "ended": call.get("endedReason"), "tools": tools})
        print(("PASS" if not failed else "FAIL"), f"{name:<8} turns={r.get('turnCount')!s:<4} ended={call.get('endedReason')} | {tools}")
        for f in failed:
            print("       -", redact(f)[:260])
    n = sum(x["pass"] for x in rows)
    (out_dir / "summary.json").write_text(redact(json.dumps({"simulation_id": a.simulation_id, "passed": n, "total": len(rows), "rows": rows}, indent=1)))
    print(f"\n{n} / {len(rows)} passed. Saved under {out_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
