"""Live tests for a built agent: one Freya simulation (chat mode) over scenario personas, results saved with transcripts.

    python devtools/final_sim.py <slot> <scenarios.json> <label> [--reps N]

scenarios.json: [{"id": "S1", "title": "...", "prompt": "who the customer is and what they do (any language)",
                  "checks": ["observable pass condition", ...], "variables": {"customerNumber": "...", "now": "..."}}]
Results: results/final-sim-<label>/summary.json (+ one transcript per run). Missing verdicts stay missing (never a pass).
"""
import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit"))
import freya  # noqa: E402


def transcript(call):
    lines = []
    for m in call.get("messages", []):
        role = m.get("role")
        if m.get("toolCalls") or m.get("tool_calls"):
            for tc in m.get("toolCalls") or m.get("tool_calls"):
                fn = tc.get("function") or tc
                lines.append(f"[tool call] {fn.get('name')} {fn.get('arguments')}")
        if role in ("tool", "tool_call_result", "function"):
            lines.append(f"[tool result] {str(m.get('result') or m.get('content') or m.get('message'))[:600]}")
        elif m.get("message") or m.get("content"):
            lines.append(f"{role}: {m.get('message') or m.get('content')}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slot")
    ap.add_argument("scenarios")
    ap.add_argument("label")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--collect", help="read-only: collect an existing simulation id (personas are matched by name)")
    a = ap.parse_args()
    agent_id = json.loads((ROOT / ".freya-agents.json").read_text())[a.slot]["agent_id"]
    scen = json.loads(pathlib.Path(a.scenarios).read_text())
    specs, varmap, keyof = [], {}, {}
    for rep in range(1, a.reps + 1):
        for s in scen:
            specs.append({"name": f"{s['id']} {s['title']} #r{rep}", "prompt": s["prompt"],
                          "successCriteria": "All of these are true:\n- " + "\n- ".join(s["checks"]), "tags": ["final-case", f"rep-{rep}"]})
    made = freya.create_personas(specs)["personas"] if not a.collect else [{"id": None}] * len(specs)
    i = 0
    for rep in range(1, a.reps + 1):
        for s in scen:
            pid = made[i]["id"]
            varmap[pid] = {k: str(v) for k, v in s["variables"].items()}
            keyof[pid] = f"{s['id']}#r{rep}"
            i += 1
    out = ROOT / "results" / f"final-sim-{a.label}"
    out.mkdir(parents=True, exist_ok=bool(a.collect))
    t0 = time.time()
    if a.collect:
        sim_id = a.collect
        its = freya.api("GET", f"/api/v2/simulations/{sim_id}/iterations")
        its = its.get("iterations") or its.get("items") or its
        it_id, status = its[0]["id"], "collected"
    else:
        sim_id, it_id, status = freya.run_simulation(agent_id, f"final {a.label}", varmap, max_seconds=900)
    it = freya.get_iteration(sim_id, it_id)
    rows = []
    for r in it.get("runs", []):
        pid = r.get("personaId") or (r.get("persona") or {}).get("id")
        key = keyof.get(pid) or str(r.get("personaName") or (r.get("persona") or {}).get("name") or pid)
        verdict = r.get("status") or r.get("result")
        call_id = r.get("callId") or (r.get("call") or {}).get("id")
        text = ""
        if call_id:
            try:
                call = freya.get_call(call_id)
                text = transcript(call)
                (out / f"{key.replace('#', '_')}.json").write_text(json.dumps(call, indent=1, ensure_ascii=False))
            except RuntimeError as e:
                text = f"(call not readable: {e})"
        (out / f"{key.replace('#', '_')}.txt").write_text(text)
        rows.append({"key": key, "verdict": verdict, "analysis": r.get("analysis") or r.get("reason") or r.get("evaluation"),
                     "call_id": call_id, "variables": varmap.get(pid)})
    summary = {"slot": a.slot, "agent_id": agent_id, "simulation_id": sim_id, "iteration_id": it_id, "status": status, "seconds": round(time.time() - t0),
               "agent_version": it.get("agentVersionNumber"), "runs": rows}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    for row in rows:
        print(row["key"], row["verdict"], str(row["analysis"])[:200])
    print("saved", out)


if __name__ == "__main__":
    main()
