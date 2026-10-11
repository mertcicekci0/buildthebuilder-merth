"""Check a submission commit before sending the form (read-only; no network).

    python devtools/verify_submission.py hidden-1 hidden-2            # brief names = trace/workflow file stems
    python devtools/verify_submission.py hidden-1 --commit HEAD

For each brief N it checks, at the given commit (default HEAD):
- submission/N.workflow.json, traces/N.log and traces/N.jsonl exist and are tracked by git;
- the trace's START line names one code commit C, without "+dirty"; all briefs share the same C;
- the trace ended with status ok, made at least one model call, saved a workflow, and exported it;
- the exported workflow's workflow_id/agent_id match the trace's "saved" line, and its sha matches the "exported" line;
- the run took at most 30 minutes;
- between C and the commit only artefact paths changed (submission/, traces/, out/, docs/), so every execution-relevant
  file of the generating code commit is preserved byte for byte;
- devtools/secret_scan.py finds nothing in submission/ and traces/.
Exit code 1 on any problem.
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARTEFACT_PREFIXES = ("submission/", "traces/", "out/", "docs/")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("briefs", nargs="+")
    ap.add_argument("--commit", default="HEAD")
    a = ap.parse_args(argv)
    problems, code_commits = [], set()
    tracked = set(git("ls-tree", "-r", "--name-only", a.commit).split())
    for n in a.briefs:
        wf_p, log_p, jl_p = f"submission/{n}.workflow.json", f"traces/{n}.log", f"traces/{n}.jsonl"
        for f in (wf_p, log_p, jl_p):
            if f not in tracked:
                problems.append(f"{n}: {f} is not tracked in {a.commit}")
        try:
            events = [json.loads(x) for x in git("show", f"{a.commit}:{jl_p}").splitlines() if x.strip()]
            log = git("show", f"{a.commit}:{log_p}")
            wf_bytes = subprocess.run(["git", "show", f"{a.commit}:{wf_p}"], cwd=ROOT, capture_output=True).stdout
            wf = json.loads(wf_bytes)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{n}: cannot read artefacts: {e}")
            continue
        start = next((e for e in events if e["kind"] == "start"), {})
        end = next((e for e in events if e["kind"] == "end"), {})
        commit = str(start.get("commit", ""))
        if not commit or commit == "unknown" or "+dirty" in commit:
            problems.append(f"{n}: trace commit is {commit!r} (must be a clean commit)")
        code_commits.add(commit.replace("+dirty", ""))
        if "status=ok" not in end.get("text", ""):
            problems.append(f"{n}: trace end is {end.get('text')!r}")
        m = re.search(r"duration_s=(\d+)", end.get("text", ""))
        if not m or int(m.group(1)) > 30 * 60:
            problems.append(f"{n}: run duration {m.group(1) if m else '?'} s is missing or above 30 minutes")
        if not any(e["kind"] == "model" and e.get("status") == "success" for e in events):
            problems.append(f"{n}: no successful model call in the trace")
        saved = [e for e in events if e["kind"] == "freya" and "workflow_id" in e]
        exported = [e for e in events if e["kind"] == "freya" and e["text"].startswith("exported")]
        if not saved or not exported:
            problems.append(f"{n}: trace lacks the saved/exported lines")
        else:
            if saved[-1].get("workflow_id") != wf.get("workflow_id") or saved[-1].get("agent_id") != wf.get("agent_id"):
                problems.append(f"{n}: workflow/agent id in the export differ from the trace")
            want = re.search(r"sha=([0-9a-f]+)", exported[-1]["text"])
            if want and hashlib.sha256(wf_bytes).hexdigest()[:len(want.group(1))] != want.group(1):
                problems.append(f"{n}: export sha differs from the trace (file changed after the run?)")
        if "START" not in log or "END" not in log:
            problems.append(f"{n}: {log_p} does not look like the builder's stdout")
    if len(code_commits) > 1:
        problems.append(f"briefs were built from different commits: {sorted(code_commits)}")
    for c in code_commits:
        if not c or c == "unknown":
            continue
        changed = [f for f in git("diff", "--name-only", c, a.commit).split() if f]
        bad = [f for f in changed if not f.startswith(ARTEFACT_PREFIXES)]
        if bad:
            problems.append(f"files differ between code commit {c} and {a.commit} outside artefact paths: {bad[:10]}")
    scan = subprocess.run([sys.executable, str(ROOT / "devtools/secret_scan.py"), str(ROOT / "submission"), str(ROOT / "traces")],
                          capture_output=True, text=True)
    if scan.returncode != 0 or scan.stdout.strip():
        problems.append("secret scan: " + scan.stdout.strip()[:300])
    for p in problems:
        print("PROBLEM", p)
    print(f"{len(problems)} problem(s); code commit(s): {sorted(code_commits)}; checked commit: {git('rev-parse', '--short', a.commit).strip()}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
