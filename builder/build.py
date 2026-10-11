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
from builder import inputs as bi  # noqa: E402
from builder import local_exec as le  # noqa: E402
from builder import openapi_tools as ot  # noqa: E402
from builder import policy_schema as ps  # noqa: E402
from builder import validate as v  # noqa: E402
from builder.trace import Trace  # noqa: E402

DEADLINE_MIN = float(os.environ.get("BUILDER_DEADLINE_MIN", "27"))   # hard stop below the 30-minute limit
T0 = float(os.environ.get("BUILDER_T0") or time.time())             # kept across a runtime re-exec
HARD_LIMIT_S = float(os.environ.get("BUILDER_HARD_LIMIT_S", str(29 * 60)))   # whole process, below the official 30 minutes


class BuilderTimeout(BaseException):   # not an Exception: generic handlers (bounded retries) must not swallow it
    pass


def arm_watchdog():
    """Whole-process limit measured from T0 (the first interpreter, before any re-exec). At HARD_LIMIT_S the run is
    interrupted inside Python (traced as a timeout); 45 s later a thread ends the process unconditionally."""
    import signal
    import threading
    left = T0 + HARD_LIMIT_S - time.time()
    if left <= 1:
        raise BuilderTimeout("no time left before the hard limit")

    def on_alarm(signum, frame):
        raise BuilderTimeout(f"hard limit of {HARD_LIMIT_S / 60:.1f} minutes since start reached")
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, on_alarm)
        signal.setitimer(signal.ITIMER_REAL, left)

    def last_resort():
        time.sleep(max(1.0, left + 45))
        print(f"[watchdog] hard limit exceeded by 45 s; exiting", flush=True)
        os._exit(3)
    threading.Thread(target=last_resort, daemon=True).start()


PINNED_SDK = "0.2.165"   # = pyproject.toml / uv.lock


def _installed_sdk(python=None):
    """Version of claude-agent-sdk importable by `python` (default: this interpreter), or None."""
    if python is None:
        try:
            import importlib.metadata
            import claude_agent_sdk  # noqa: F401
            return importlib.metadata.version("claude-agent-sdk")
        except Exception:  # noqa: BLE001
            return None
    import subprocess
    try:
        r = subprocess.run([str(python), "-c", "import importlib.metadata as m, claude_agent_sdk; print(m.version('claude-agent-sdk'))"],
                           capture_output=True, text=True, timeout=120)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def ensure_model_runtime():
    """The model boundary needs claude-agent-sdk (pinned version). Use this interpreter if it has that version; otherwise
    the repo's .venv, creating or repairing it from the lock files (`uv sync --frozen`, else pip --require-hashes on
    Python >= 3.10), and re-run this command there. If that fails but this interpreter has another SDK version, continue
    with it and say so. Every step is printed."""
    import shutil
    import subprocess
    here = _installed_sdk()
    if here == PINNED_SDK:
        return
    if os.environ.get("BUILDER_REEXEC") == "1":
        if here:
            print(f"[runtime] warning: claude-agent-sdk {here} in .venv (pinned {PINNED_SDK}); continuing", flush=True)
            return
        raise SystemExit("claude-agent-sdk is not importable even in .venv; run `uv sync --frozen` and retry")
    venv_py = ROOT / ".venv" / "bin" / "python"
    print(f"[runtime] {sys.executable} has claude-agent-sdk {here or 'not installed'} (pinned {PINNED_SDK})", flush=True)
    ok = venv_py.exists() and _installed_sdk(venv_py) == PINNED_SDK
    if not ok:
        existed = (ROOT / ".venv").exists()
        print("[runtime] preparing .venv from the lock files", flush=True)
        budget = max(60, min(900, T0 + HARD_LIMIT_S - time.time() - 10 * 60))   # leave >= 10 minutes for the build itself
        try:
            if shutil.which("uv"):
                ok = subprocess.run(["uv", "sync", "--frozen", "--no-dev"], cwd=ROOT, timeout=budget).returncode == 0
            if not ok and sys.version_info >= (3, 10):
                if not venv_py.exists():
                    subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], timeout=min(300, budget))
                ok = venv_py.exists() and subprocess.run([str(venv_py), "-m", "pip", "install", "-q", "--require-hashes", "-r",
                                                          str(ROOT / "requirements.hashes.txt")], timeout=budget).returncode == 0
        except Exception as e:  # noqa: BLE001
            print(f"[runtime] preparing .venv failed: {e}", flush=True)
            ok = False
        ok = ok and _installed_sdk(venv_py) == PINNED_SDK
        if not ok and not existed and (ROOT / ".venv").exists():
            shutil.rmtree(ROOT / ".venv", ignore_errors=True)   # never leave a half-built .venv behind
    if ok:
        print(f"[runtime] re-running with .venv/bin/python (claude-agent-sdk {PINNED_SDK})", flush=True)
        env = dict(os.environ, BUILDER_REEXEC="1", BUILDER_T0=str(T0))
        import signal
        if hasattr(signal, "setitimer"):
            signal.setitimer(signal.ITIMER_REAL, 0)   # the re-executed process re-arms from the same T0
        os.execve(str(venv_py), [str(venv_py), str(pathlib.Path(__file__).resolve())] + sys.argv[1:], env)
    if here:
        print(f"[runtime] warning: could not prepare .venv; continuing with claude-agent-sdk {here}", flush=True)
        return
    raise SystemExit("claude-agent-sdk is not available and .venv could not be prepared: install uv and run `uv sync --frozen` "
                     "(or, on Python >= 3.10, pip install -r requirements.hashes.txt --require-hashes)")


def _sdk_version():
    return _installed_sdk() or "not installed"


def _git_commit():
    try:
        import subprocess
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        # dirty = a change to execution-relevant files only (artefacts of earlier runs do not count; untracked code does)
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "builder", "kit", "pyproject.toml", "uv.lock", "requirements.hashes.txt",
                                "requirements.lock.txt", ".python-version"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
        return (out.stdout.strip() or "unknown") + ("+dirty" if dirty else "")
    except Exception:  # noqa: BLE001
        return "unknown"


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
    ap.add_argument("--tool-headers", help="JSON file {header: value} sent with every tool call (e.g. auth the contract requires; values come from the case or the organisers)")
    ap.add_argument("--tool-map", help="JSON file {canonical tool: operationId or 'METHOD /path'} for a contract with other operation names")
    a = ap.parse_args()
    try:
        tool_map = json.load(open(a.tool_map)) if a.tool_map else None
        tool_headers = json.load(open(a.tool_headers)) if a.tool_headers else {}
    except (OSError, ValueError) as e:
        raise SystemExit(f"cannot read the tool map / headers file: {e}")
    if not isinstance(tool_headers, dict) or not all(isinstance(v, str) for v in tool_headers.values()):
        raise SystemExit("--tool-headers must be a JSON object of string values")
    mock_base = os.environ.get("BUILDER_BASE_URL_OVERRIDE", "").strip()   # test builds against our own mock: never a submission
    arm_watchdog()
    if not a.ir and os.environ.get("BUILDER_LLM_BACKEND", "agent_sdk") == "agent_sdk":
        ensure_model_runtime()
    slot = a.slot or pathlib.Path(a.brief_path).stem
    if mock_base and not slot.endswith("-mock"):
        slot += "-mock"
    for d in ("out", "submission", "traces"):
        (ROOT / d).mkdir(exist_ok=True)
    trace_dir = ROOT / os.environ.get("BUILDER_TRACE_DIR", "traces")   # development runs use traces/dev (ignored)
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace = Trace(trace_dir / f"{slot}.jsonl", t0=T0)
    deadline = T0 + DEADLINE_MIN * 60
    trace.start(brief=a.brief_path, brief_sha=bi.sha_path(a.brief_path), tool_api=a.tool_api_path, api_sha=bi.sha_path(a.tool_api_path), slot=slot,
                tool_map=tool_map, tool_headers=sorted(tool_headers), base_url_override=mock_base or None,
                builder_model=os.environ.get("BUILDER_MODEL", "claude-opus-5-5"), llm_backend=os.environ.get("BUILDER_LLM_BACKEND", "agent_sdk"),
                python=sys.version.split()[0], agent_sdk=_sdk_version(), commit=_git_commit(), started_before_s=round(time.time() - T0, 1))
    # the phone-agent model is the event's (kit/freya.py AGENT_LLM); organisers may override it by env, so it is traced, never changed
    trace.step("phone-agent model", agent_llm={"provider": os.environ.get("FREYA_AGENT_PROVIDER", "experimental-5"),
                                                "model": os.environ.get("FREYA_AGENT_MODEL", "experimental-5")},
               overridden=bool(os.environ.get("FREYA_AGENT_PROVIDER") or os.environ.get("FREYA_AGENT_MODEL")))
    status = "failed"
    if not a.no_save and not os.environ.get("FREYA_API_KEY"):   # fail before the model calls, not after them
        trace.end("failed", error="FREYA_API_KEY is not set")
        raise SystemExit("FREYA_API_KEY is not set (source env.sh); nothing was built")
    try:
        contract = bi.find_contract(a.tool_api_path, ROOT / "out" / "inputs" / f"{slot}-contract")
        brief, files = bi.read_brief(a.brief_path, ROOT / "out" / "inputs" / slot, exclude=[contract])
        trace.step(f"inputs read: {len(files)} file(s), brief text {len(brief)} chars, contract {contract.name}", files=files)
        api = ot.load(str(contract), tool_map=tool_map, base_url=mock_base or None)
        if api["unknown_operations"]:
            trace.warn("contract operations not used by the builder: " + ", ".join(api["unknown_operations"]))
        api["static_headers"] = tool_headers
        missing_auth = [n for n, s in api["auth"].items() if not (s.get("type") == "apiKey" and s.get("in") == "header" and s.get("name") in tool_headers)
                        and not (s.get("type") in ("http", "oauth2", "openIdConnect") and "Authorization" in tool_headers)]
        if missing_auth:
            trace.warn(f"the contract requires auth {api['auth']} but --tool-headers does not provide it: every tool call may be rejected")
        params = sorted({p for c in api["tools"].values() for p in c.get("path_params", [])})
        if params:
            trace.warn(f"path parameters {params} are filled from call variables of the same name ({{{{name}}}}); confirm they exist")
        if api["absent_optional"]:
            trace.warn("optional tools absent from the contract (their paths are dropped): " + ", ".join(api["absent_optional"]))
        roles, role_problems = ot.infer_roles(api)
        trace.step(f"tool API parsed: {len(api['tools'])} tools at {api['base_url']}", roles=roles, problems=role_problems)
        if api["missing_tools"]:
            raise SystemExit(f"tool API is missing {api['missing_tools']}")
        if role_problems:
            trace.warn("field roles not recognised by name: " + "; ".join(role_problems))

        if a.ir:
            ir = {k: val for k, val in json.load(open(a.ir)).items() if not k.startswith("_")}
            check_warnings = []
            problems = ps.check(ir, api, roles, brief, warnings=check_warnings)
            trace.step(f"policy IR loaded from {a.ir} (no model call)")
            trace.test("policy checks", "pass" if not problems else f"{len(problems)} problem(s)", problems=problems, warnings=check_warnings)
            for w in check_warnings:
                trace.warn("policy check: " + w)
        else:
            from builder.extract import extract
            from builder.llm import LLM
            ir, problems, _ = extract(LLM(trace, deadline=deadline - 8 * 60), brief, api, roles, trace)
        (ROOT / "out" / f"{slot}.policy.json").write_text(json.dumps(ir, indent=1))
        if problems:
            trace.warn("continuing with unresolved policy problems: " + "; ".join(problems[:10]))

        from builder.contract_checks import contract_warnings
        cwarn = contract_warnings(api, roles, ir)
        for x in cwarn:
            trace.warn("contract: " + x)
        for x in (ir.get("unmapped_rules") or []):
            trace.warn("policy rule not expressible in the IR (prompt text only): " + str(x)[:300])
        compile_warnings = []
        wf = cw.compile_workflow(ir, api, roles, warnings=compile_warnings)
        for w in compile_warnings:
            trace.warn("compile: " + w)
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
        trace.created(f"save returned workflow_id={res.get('workflow_id')} agent_id={res.get('agent_id')} version={res.get('version')}")
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
        out = ROOT / ("out" if mock_base else "submission") / f"{slot}.workflow.json"   # a mock-URL build is never a submission
        freya.export_workflow(res["workflow_id"], str(out))
        trace.created(f"exported to {out.relative_to(ROOT)} sha={sha(out)}")
        status = "ok" if not (fails or missing or lost_tools or cwarn) else "ok with warnings"
        return 0
    except BuilderTimeout as e:
        trace.warn(f"timeout: {e}")
        status = "timeout"
        return 1
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
