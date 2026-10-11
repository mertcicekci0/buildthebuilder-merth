"""Write submission.json with the official fields (freyavoice.ai/hackathon, read 2026-10-11 16:28 UTC): team, members [{name, email}],
repo, commit, run_command, workflow, trace, mock_url. Every path must exist in the given commit; nothing is sent anywhere.

    python devtools/make_submission.py --team T --member "Name <email>" --repo URL --commit S --mock-url URL [--out submission.json]
"""
import argparse
import json
import re
import subprocess

RUN_COMMAND = "python3 builder/build.py {case_folder} {mock_url}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True)
    ap.add_argument("--member", action="append", required=True, help='"Full Name <email>" (repeat per member)')
    ap.add_argument("--repo", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--mock-url", required=True)
    ap.add_argument("--workflow", default="submission/final.workflow.json")
    ap.add_argument("--trace", default="traces/final.log")
    ap.add_argument("--out", default="submission.json")
    a = ap.parse_args()
    members = []
    for m in a.member:
        mm = re.fullmatch(r"\s*(.+?)\s*<\s*([^<>@\s]+@[^<>\s]+)\s*>\s*", m)
        if not mm:
            raise SystemExit(f"member must be 'Name <email>': {m!r}")
        members.append({"name": mm.group(1), "email": mm.group(2)})
    full = subprocess.run(["git", "rev-parse", "--verify", a.commit + "^{commit}"], capture_output=True, text=True)
    if full.returncode:
        raise SystemExit(f"unknown commit {a.commit}")
    for path in (a.workflow, a.trace):
        if subprocess.run(["git", "cat-file", "-e", f"{a.commit}:{path}"], capture_output=True).returncode:
            raise SystemExit(f"{path} is not in commit {a.commit}")
    if not re.match(r"^https://[^/\s]+", a.mock_url):
        raise SystemExit("mock_url must be an https URL")
    sub = {"team": a.team, "members": members, "repo": a.repo, "commit": full.stdout.strip()[:12], "run_command": RUN_COMMAND,
           "workflow": a.workflow, "trace": a.trace, "mock_url": a.mock_url}
    open(a.out, "w").write(json.dumps(sub, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(sub, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
