"""Run practice customers against a phone agent and score them, with the extras we need for development.

    .venv/bin/python devtools/run_cases_ext.py --agent <agent_id> --brief A
    .venv/bin/python devtools/run_cases_ext.py --slot probe --cases cases/b_variants.json --reps 3 --styles pieces,corrects,in_words
    .venv/bin/python devtools/run_cases_ext.py --agent <id> --brief B --now 2026-10-16T15:00 --deadline-min 20

Differences from kit/tools/run_cases.py (whose persona text and ledger scoring are reused unchanged):
- all repetitions run inside ONE simulation, each repetition with its own persona and its own bank session,
  so a case's runs never share a ledger and the single simulation slot is used once;
- --styles assigns a speech style per repetition (cycled); a case may also carry its own "style";
- --now overrides the `now` call variable (other calendar days); a case may carry "now_override";
- a case file may hold custom scripts and expectations over the hosted fixtures (the bank keys data by case id);
- a hard --deadline-min on waiting, so a test can never hang a builder run;
- transcripts of failed runs (or all, --fetch all) are saved under results/<label>/calls/.
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit"))
sys.path.insert(0, str(ROOT / "kit" / "tools"))
sys.path.insert(0, str(ROOT / "devtools"))
import freya  # noqa: E402
from run_cases import STYLES, persona_for  # noqa: E402  (kit, unchanged)
from score import score_ledger  # noqa: E402            (kit, unchanged)
import bank  # noqa: E402
from fetch_call import timeline  # noqa: E402
from redact import redact  # noqa: E402

PERSONA_STORE = ROOT / ".personas.json"


def load_cases(paths, brief, only):
    cases = []
    for p in paths:
        cases += json.load(open(p))
    if brief:
        cases = [c for c in cases if c["brief"] == brief]
    if only:
        keep = set(only.split(","))
        cases = [c for c in cases if c["id"] in keep or c.get("run_id") in keep]
    return cases


def persona_ids(specs):
    """Create personas whose text is new; reuse identical ones. specs: {key: persona_dict}."""
    store = json.loads(PERSONA_STORE.read_text()) if PERSONA_STORE.exists() else {}
    digest = {k: hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest()[:12] for k, p in specs.items()}
    new = [k for k in specs if digest[k] not in store]
    for i in range(0, len(new), 25):
        chunk = new[i:i + 25]
        made = freya.create_personas([specs[k] for k in chunk])["personas"]
        for k, p in zip(chunk, made):
            store[digest[k]] = p["id"]
        PERSONA_STORE.write_text(json.dumps(store, indent=1))
    return {k: store[digest[k]] for k in specs}


def start_simulation(agent_id, name, variables_by_persona, concurrency, max_call_s, deadline):
    while True:
        try:
            sim = freya.api("POST", "/api/v2/simulations", {
                "agentId": agent_id, "name": name, "mode": "chat", "selectedPersonaIds": list(variables_by_persona),
                "customerVariablesMap": variables_by_persona, "maxConcurrentCalls": concurrency, "runsPerPersona": 1,
                "maxCallDurationSeconds": max_call_s,
                "assistantLlmOverride": freya.AGENT_LLM, "routingLlmOverride": freya.AGENT_LLM, "extractionLlmOverride": freya.AGENT_LLM,
                "personaLlmOverride": freya.SIM_LLM, "analysisLlmOverride": freya.SIM_LLM})
            return (sim.get("simulation") or sim)["id"]
        except RuntimeError as e:
            if "concurrent simulations" not in str(e) or time.time() > deadline:
                raise
            print("another simulation is running in this workspace, waiting 30 s", flush=True)
            time.sleep(30)


def api_retry(method, path, deadline, **kw):
    """freya.api with retries on transient 5xx (the platform returned 503/504 under load on 2026-10-10)."""
    while True:
        try:
            return freya.api(method, path, **kw)
        except (RuntimeError, OSError) as e:   # OSError covers URLError / connection resets
            transient = isinstance(e, OSError) or any(f"-> {c}" in str(e) for c in ("500", "502", "503", "504"))
            if not transient or time.time() > deadline:
                raise
            print("transient platform error, retrying in 20 s:", redact(str(e))[:120], flush=True)
            time.sleep(20)


def wait_simulation(sim_id, deadline):
    status = "unknown"
    while time.time() < deadline:
        time.sleep(10)
        s = api_retry("GET", f"/api/v2/simulations/{sim_id}", deadline)
        s = s.get("simulation") or s
        status = s.get("status")
        if status in ("completed", "failed", "paused"):
            return status
    # Free the workspace's single simulation slot (verified 2026-10-10: PUT status=completed ends a stuck simulation).
    try:
        freya.api("PUT", f"/api/v2/simulations/{sim_id}", {"status": "completed"})
    except RuntimeError as e:
        print("could not stop simulation:", redact(str(e))[:200])
    return f"deadline({status})"


def slowest_turn(call):
    last_user, worst = None, 0.0
    for m in call.get("messages", []):
        t = m.get("secondsFromStart")
        if t is None:
            continue
        if m.get("role") == "user":
            last_user = t
        elif last_user is not None:
            worst = max(worst, t - last_user)
            last_user = None if m.get("role") == "assistant" else last_user
    return round(worst, 1)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=False)
    g.add_argument("--agent")
    g.add_argument("--slot", help="slot name in .freya-agents.json")
    ap.add_argument("--cases", nargs="+", default=[str(ROOT / "kit" / "public-test-set.json")])
    ap.add_argument("--brief")
    ap.add_argument("--only")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--styles", default="", help="comma list cycled over repetitions; 'plain' = no style")
    ap.add_argument("--now", help="override the now call variable for every run, e.g. 2026-10-16T15:00")
    ap.add_argument("--concurrency", type=int, default=int(os.environ.get("FREYA_SIM_CONCURRENCY", "5")))
    ap.add_argument("--max-call-seconds", type=int, default=420)
    ap.add_argument("--deadline-min", type=float, default=30)
    ap.add_argument("--label", default="")
    ap.add_argument("--fetch", choices=["failed", "all", "none"], default="failed")
    ap.add_argument("--collect", action="store_true", help="do not start anything: wait for and score the run saved under --label")
    a = ap.parse_args()

    agent_id = None if a.collect else a.agent or json.loads((ROOT / ".freya-agents.json").read_text())[a.slot]["agent_id"]
    cases = [] if a.collect else load_cases(a.cases, a.brief, a.only)
    styles = [s.strip() for s in a.styles.split(",") if s.strip()] or [None]
    label = a.label or time.strftime("%m%d-%H%M%S")
    out_dir = ROOT / "results" / label
    (out_dir / "calls").mkdir(parents=True, exist_ok=True)
    deadline = time.time() + a.deadline_min * 60

    plan_file = out_dir / "plan.json"
    if a.collect:
        plan = json.loads(plan_file.read_text())
        runs, sim_id, agent_id = plan["runs"], plan["simulation_id"], plan["agent_id"]
        t0 = time.time()
        return finish(a, runs, sim_id, agent_id, cases_from(runs), label, out_dir, deadline, t0)
    runs = []  # one entry per (case, rep)
    for rep in range(a.reps):
        for c in cases:
            style = c.get("style") or styles[rep % len(styles)]
            style = None if style in (None, "plain") else style
            if style and style not in STYLES:
                sys.exit(f"unknown style {style}; choose from {sorted(STYLES)}")
            spec = persona_for(c, {"identity": c["identity"]}, style)
            spec["name"] += f" #r{rep + 1}"
            spec["tags"] = spec.get("tags", []) + [f"rep-{rep + 1}"]
            runs.append({"key": f"{c.get('run_id', c['id'])}#r{rep + 1}", "case": c, "rep": rep + 1, "style": style, "spec": spec})

    ids = persona_ids({r["key"]: r["spec"] for r in runs})
    variables = {}
    for r in runs:
        st, sess = bank.session(r["case"]["id"])
        if st != 200 or "variables" not in sess:
            sys.exit(f"bank session for {r['case']['id']} failed: {st} {sess}")
        v = dict(sess["variables"])
        now = r["case"].get("now_override") or a.now
        if now:
            v["now"] = now
        r["variables"], r["persona_id"] = v, ids[r["key"]]
        variables[r["persona_id"]] = v

    t0 = time.time()
    sim_id = start_simulation(agent_id, f"ext {label}", variables, a.concurrency, a.max_call_seconds, deadline)
    plan_file.write_text(json.dumps({"agent_id": agent_id, "simulation_id": sim_id, "runs": [{k: v for k, v in r.items() if k != "spec"} for r in runs]}, indent=1))
    print(f"simulation {sim_id}: {len(runs)} calls ({len(cases)} cases x {a.reps} reps), concurrency {a.concurrency}", flush=True)
    return finish(a, runs, sim_id, agent_id, cases, label, out_dir, deadline, t0)


def cases_from(runs):
    seen = {}
    for r in runs:
        seen.setdefault(r["case"].get("run_id", r["case"]["id"]), r["case"])
    return list(seen.values())


def finish(a, runs, sim_id, agent_id, cases, label, out_dir, deadline, t0):
    status = wait_simulation(sim_id, deadline)
    took = time.time() - t0
    its = api_retry("GET", f"/api/v2/simulations/{sim_id}/iterations", deadline + 600)
    its = its.get("iterations") or its.get("items") or its
    it_id = its[0]["id"] if its else None
    judge = {x["personaId"]: x for x in (freya.get_iteration(sim_id, it_id)["runs"] if it_id else [])}

    rows, waits = [], []
    for r in runs:
        c, cid = r["case"], r["variables"]["customer_id"]
        st, led = bank.ledger(cid)
        res = score_ledger(c, led) if st == 200 and isinstance(led, dict) and "calls" in led else {"failed": [f"ledger {st}"]}
        j = judge.get(r["persona_id"], {})
        if j.get("outcome") != "pass":
            res["failed"].append("conversation: " + " ".join(str(j.get("reason") or "no verdict").split())[:500])
        row = {"key": r["key"], "id": c["id"], "rep": r["rep"], "style": r["style"], "now": r["variables"].get("now"),
               "pass": not res["failed"], "failed": res["failed"], "call_id": j.get("callId"), "customer_id": cid,
               "tools": [x["tool"] for x in led.get("calls", [])] if isinstance(led, dict) else [], "ledger": led}
        if row["call_id"] and (a.fetch == "all" or (a.fetch == "failed" and not row["pass"])):
            try:
                call = freya.get_call(row["call_id"])
                row["slowest_turn_s"], row["ended"] = slowest_turn(call), call.get("endedReason")
                waits.append(row["slowest_turn_s"])
                stem = row["key"].replace("#", "_")
                (out_dir / "calls" / f"{stem}.txt").write_text(timeline(call, show_vars=True))
                (out_dir / "calls" / f"{stem}.json").write_text(redact(json.dumps(call, indent=1, ensure_ascii=False)))
            except Exception as e:  # noqa: BLE001
                row["fetch_error"] = redact(str(e))[:200]
        rows.append(row)
        tools = " > ".join(t.replace("verify_identity", "verify").replace("create_promise", "promise") for t in row["tools"]) or "(no tool calls)"
        print(("PASS" if row["pass"] else "FAIL"), f"{row['key']:<14}", f"{(r['style'] or 'plain'):<10}", "|", tools)
        for f in row["failed"]:
            print("       -", redact(f)[:300])

    per_case = {}
    for row in rows:
        per_case.setdefault(row["key"].split("#")[0], []).append(row["pass"])
    final = {k: {"passes": sum(v), "runs": len(v), "pass": all(v)} for k, v in per_case.items()}
    n = sum(f["pass"] for f in final.values())
    summary = {"label": label, "agent_id": agent_id, "simulation_id": sim_id, "iteration_id": it_id, "status": status,
               "seconds": round(took), "reps": a.reps, "styles": a.styles, "now": a.now, "cases": [c.get("run_id", c["id"]) for c in cases],
               "passed_cases": n, "total_cases": len(final), "run_pass_rate": round(sum(r["pass"] for r in rows) / max(1, len(rows)), 3),
               "final": final, "rows": rows}
    (out_dir / "summary.json").write_text(redact(json.dumps(summary, indent=1, ensure_ascii=False)))
    with open(ROOT / "results" / "log.jsonl", "a") as f:
        f.write(json.dumps({k: summary[k] for k in ("label", "agent_id", "simulation_id", "status", "seconds", "reps", "styles", "now",
                                                    "passed_cases", "total_cases", "run_pass_rate")}
                           | {"failed": [k for k, v in final.items() if not v["pass"]]}) + "\n")
    print(f"\n{n} / {len(final)} cases passed all {a.reps} run(s); run-level pass rate {summary['run_pass_rate']:.0%}. "
          f"simulation {sim_id} status {status}, {took:.0f} s. Results: {out_dir.relative_to(ROOT)}")
    if waits:
        waits.sort()
        print(f"slowest agent turn per fetched call: median {waits[len(waits) // 2]:.0f} s, worst {waits[-1]:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
