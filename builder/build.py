"""Build a Freya phone agent from one bank brief and its tool API.

    python3 builder/build.py {brief_path} {tool_api_path}            # organisers' contract
    python3 builder/build.py brief.md api.json --ir policy.json        # skip the model: compile a given IR (development)
    python3 builder/build.py brief.md api.json --no-save               # everything except writing to Freya

Needs FREYA_API_KEY (and FREYA_API_URL) in the environment, and Claude Code authentication for the model calls
(the Agent SDK uses the runner's own login or key). Writes:
  out/<slot>.policy.json, out/<slot>.workflow.json      what was extracted and compiled
  submission/<slot>.workflow.json                       freya.export_workflow of the saved agent
  traces/<slot>.jsonl                                   machine-readable twin of the stdout trace
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "kit"))

from builder import compile_workflow as cw  # noqa: E402
from builder import local_exec as le  # noqa: E402
from builder import openapi_tools as ot  # noqa: E402
from builder import policy_schema as ps  # noqa: E402
from builder import validate as v  # noqa: E402
from builder.trace import Trace  # noqa: E402

DEADLINE_MIN = float(os.environ.get("BUILDER_DEADLINE_MIN", "27"))   # hard stop below the 30-minute limit


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()[:16]


def synthetic_products(api, roles, ir):
    """One product per API product type, days late at each band's lower edge: local self-test data only."""
    item = (ot.product_item_schema(api) or {}).get("properties", {})
    pf = roles["product_fields"]
    types = (item.get(pf.get("product_type", ""), {}) or {}).get("enum") or [t["api_type"] for t in ir["product_types"]]
    bands = sorted(ir["bands"], key=lambda b: b["min"]) or [{"min": 5}]
    prods = []
    for i, t in enumerate(types[:3]):
        p = {}
        for name, sch in item.items():
            typ = sch.get("type")
            p[name] = 100.0 if typ in ("number", "integer") else (sch.get("enum") or ["1234"])[0]
        p.update({pf["product_id"]: f"p{i + 1}", pf.get("product_type", "type"): t, pf["past_due"]: 500.0,
                  pf["days_late"]: bands[i % len(bands)]["min"]})
        prods.append(p)
    return prods


def self_test(wf, roles, api, ir, trace):
    """Run the generated code locally on synthetic data. Returns the number of failures."""
    fails = 0
    prods = synthetic_products(api, roles, ir)
    now_var = (ir.get("call_variables") or {}).get("now") or "now"
    try:
        r = le.smoke(wf, roles, prods, now_var=now_var)
        ok = r["init_ok"] == "yes" and r["account_ok"] == "yes" and r["table_lines"] > 0 and "p1" in r["facts_text"]
        trace.test("local: init + account facts on synthetic products", "pass" if ok else "FAIL",
                   debt=r["debt_statement"], first_ask=r["first_ask"])
        fails += not ok
        pid = prods[0][roles["product_fields"]["product_id"]]
        dl = r["state"]["products"][pid]["deadline"]
        r2 = le.smoke(wf, roles, prods, promise=(pid, 500.0, dl), now_var=now_var)
        ok2 = "promise_sent" in r2 and r2["close_body"].get(roles["close_outcome"]) == ir["outcomes"]["promise"]
        trace.test("local: guarded promise on the deadline is sent; close outcome = promise", "pass" if ok2 else "FAIL", detail=r2.get("promise_blocked", ""))
        fails += not ok2
        import datetime
        late = (datetime.date.fromisoformat(dl) + datetime.timedelta(days=30)).isoformat()
        r3 = le.smoke(wf, roles, prods, promise=(pid, 500.0, late), now_var=now_var)
        ok3 = "promise_blocked" in r3
        trace.test("local: promise after the deadline is blocked before sending", "pass" if ok3 else "FAIL")
        fails += not ok3
    except Exception as e:  # noqa: BLE001
        trace.test("local self-test", f"FAIL (exception: {e})")
        fails += 1
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("brief_path")
    ap.add_argument("tool_api_path")
    ap.add_argument("--slot", help="Freya slot name (default: brief file name without extension)")
    ap.add_argument("--ir", help="use this policy IR instead of extracting one (no model call)")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    slot = a.slot or pathlib.Path(a.brief_path).stem
    for d in ("out", "submission", "traces"):
        (ROOT / d).mkdir(exist_ok=True)
    trace = Trace(ROOT / "traces" / f"{slot}.jsonl")
    deadline = time.time() + DEADLINE_MIN * 60
    trace.start(brief=a.brief_path, brief_sha=sha(a.brief_path), tool_api=a.tool_api_path, api_sha=sha(a.tool_api_path), slot=slot,
                builder_model=os.environ.get("BUILDER_MODEL", "claude-opus-5-5"), llm_backend=os.environ.get("BUILDER_LLM_BACKEND", "agent_sdk"))
    status = "failed"
    try:
        brief = pathlib.Path(a.brief_path).read_text(encoding="utf-8", errors="replace")
        api = ot.load(a.tool_api_path)
        roles, role_problems = ot.infer_roles(api)
        trace.step(f"tool API parsed: {len(api['tools'])} tools at {api['base_url']}", roles=roles, problems=role_problems)
        if api["missing_tools"]:
            raise SystemExit(f"tool API is missing {api['missing_tools']}")
        if role_problems:
            trace.warn("field roles not recognised by name: " + "; ".join(role_problems))

        if a.ir:
            ir = {k: val for k, val in json.load(open(a.ir)).items() if not k.startswith("_")}
            problems = ps.check(ir, api, roles, brief)
            trace.step(f"policy IR loaded from {a.ir} (no model call)")
            trace.test("policy checks", "pass" if not problems else f"{len(problems)} problem(s)", problems=problems)
        else:
            from builder.extract import extract
            from builder.llm import LLM
            ir, problems, _ = extract(LLM(trace, deadline=deadline - 8 * 60), brief, api, roles, trace)
        (ROOT / "out" / f"{slot}.policy.json").write_text(json.dumps(ir, indent=1))
        if problems:
            trace.warn("continuing with unresolved policy problems: " + "; ".join(problems[:10]))

        wf = cw.compile_workflow(ir, api, roles)
        lint = v.lint(wf, api)
        st = v.stats(wf)
        trace.test("workflow lint", "pass" if not lint else f"{len(lint)} problem(s)", problems=lint)
        (ROOT / "out" / f"{slot}.workflow.json").write_text(json.dumps(wf, indent=1))
        trace.step(f"workflow compiled: {st['steps']} steps, {st['tools']} tools, {st['bytes']} bytes")
        fails = self_test(wf, roles, api, ir, trace)
        if lint:
            raise SystemExit("lint failed; not saving")
        if a.no_save:
            status = "compiled (not saved)" if not fails else "compiled with self-test failures (not saved)"
            return 0
        import freya
        trace.test("platform dry run", json.dumps(freya.validate_workflow(slot, wf)))
        res = freya.save_workflow(slot, wf)
        if "version" not in res:   # first creation: update once so agent_config gets the event model and it is published
            res = freya.save_workflow(slot, wf)
        trace.created(f"saved workflow_id={res['workflow_id']} agent_id={res['agent_id']} version={res.get('version')} steps={st['steps']} tools={st['tools']}", **res)
        stored = freya.api("GET", f"/api/v2/workflow/{res['workflow_id']}", workspaceId=freya.workspace_id())["workflow"]["workflow"]
        missing = [n for n in wf["nodes"] if n not in stored.get("nodes", {})]
        lost_tools = [n for n, node in wf["nodes"].items() if len(node.get("tools") or []) != len((stored.get("nodes", {}).get(n) or {}).get("tools") or [])]
        trace.test("read-back: every node and tool stored", "pass" if not (missing or lost_tools) else f"FAIL missing={missing} tools={lost_tools}")
        vs = freya.api("GET", f"/api/v2/versions/workflow/{res['workflow_id']}", workspaceId=freya.workspace_id())["versions"]
        newest = max(vs, key=lambda x: x["versionNumber"])
        trace.test("published version is the newest", "pass" if newest.get("isPublished") else "FAIL", version=newest["versionNumber"])
        out = ROOT / "submission" / f"{slot}.workflow.json"
        freya.export_workflow(res["workflow_id"], str(out))
        trace.created(f"exported to {out.relative_to(ROOT)} sha={sha(out)}")
        status = "ok" if not (fails or missing or lost_tools) else "ok with warnings"
        return 0
    except SystemExit as e:
        trace.warn(str(e))
        return 1
    except Exception as e:  # noqa: BLE001
        trace.warn(f"{type(e).__name__}: {e}")
        return 1
    finally:
        trace.end(status)


if __name__ == "__main__":
    sys.exit(main())
