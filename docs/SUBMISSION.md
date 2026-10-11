# Submission procedure and requirements

Source: official page https://www.freyavoice.ai/hackathon (fetched 2026-10-11 01:43 UTC; Last-Modified Sat 10 Oct 2026 13:52:46 GMT,
ETag "18634eec59eafb2320a436392e82cd77") and kit/README.md ("The final", "Rules").

## What the rules require (quoted)
- Section title: "Two phone agents, one commit and the builder's trace".
- "Run your builder on each brief and keep everything it prints. You may run it more than once. Submit one phone agent per brief,
  with the trace of the run that made it. Do not edit a phone agent after the run."
- "Export each phone agent to a file with freya.export_workflow and commit the files."
- Form (send before 20:00 UTC to the address in the kit email): `team`, `repo`, `commit`, `run_command`,
  `workflows` (example `submission/hidden-1.workflow.json`), `traces` (example `traces/hidden-1.log`), produced by
  `python3 builder/build.py briefs/hidden-1.md briefs/hidden-1.openapi.json 2>&1 | tee traces/hidden-1.log`.
- Trace: "We ask for one per hidden brief. Commit the files to your repository." Content: start/end time, every model call (model +
  one-line purpose), what it created on Freya (workflow id, agent id, steps, tools), any self-test with result. "Remove API keys before you commit."
- "The phone agents for the hidden briefs must come out of the submitted commit with no manual edits." "An entry without traces is not ranked."
- Top ten: "clone the repository at that commit, run the builder ourselves on the same briefs and compare with your trace", plus a
  reviewer agent that "checks it against the submitted code and the phone agent it claims to have created".
- "You may fix your builder after the hidden briefs open. Nothing from a hidden brief may be written into it. A reviewer checks the commit for that."
- Optional: coding-agent session logs may be shared.

**Commit history:** nothing on the page or in the kit asks for regular development commits. What is required is one reproducible
commit that contains the builder, the exported workflows and the per-brief traces, reachable in a repo the organisers can clone.
(Inference: a readable history with the builder frozen before 17:00 UTC and any later bug-fix commits separate makes the
"nothing from a hidden brief" review easy, so we keep focused real commits, never backdated or synthetic.)

## Repository state (updated 2026-10-11 ~06:30 Istanbul)
- Local only: `master` = baseline a0e7f68 (tag `baseline-v4`); development on `dev/overnight` (focused commits, see the log in docs/OVERNIGHT_PLAN.md).
- **No git remote is configured. Creating the GitHub repository (and its visibility/access for the organisers) and pushing is a task for Mert**; nothing is pushed by the agent.
- `tests/test_devtools.py` is intentionally untracked (listed in .git/info/exclude): its fake key-shaped strings trip the pre-commit
  secret scan, and editing them was refused by the auto-mode classifier. Decide: keep it out, or rewrite the fixtures by hand.

## Ignore rules checked
`git check-ignore` on the final paths: `traces/<brief>.log` and `traces/<brief>.jsonl` are committable (negation rules in .gitignore),
`submission/<brief>.workflow.json` is committable (submission/ is not ignored), `traces/dev/` and `out/` are ignored
(`out/<brief>.policy.json` can be added with `git add -f` if we want to include the extracted policy).
Dev runs also write `submission/cand-*.workflow.json` etc.; commit only the hidden-brief files.

## Final-run procedure (Sunday after 17:00 UTC)
**Checked one-command version** (does steps 1-4 below, refuses to run on a dirty code tree or failing tests, archives any earlier attempt
under archive/final-runs/ instead of deleting it, never pushes):
```bash
cd /Users/mertcicekci/Documents/ChatGPT/freyahackathon && git checkout <selected branch/commit> && set -a && . ./env.sh && set +a
sh devtools/final_run.sh --commit briefs/hidden-1.md briefs/hidden-1.openapi.json briefs/hidden-2.md briefs/hidden-2.openapi.json
```
(Use the hidden files' real names; the trace/export names follow the brief file names.) Without `--commit` it stops after the checks and
prints the exact `git add`/`git commit`/`verify_submission.py` commands. Then step 5 (push + form) is manual.

Pre-final checklist (from docs/RESOURCE_AUDIT.md):
- Kit still current: the official page / organiser email names kit VERSION 3 (README L3: "when we update the kit, the number goes up"),
  and `(cd kit && shasum -a 256 -c ../kit.sha256)` prints OK for all 16 files. A newer kit (changed freya.py) must be reviewed before the run.
- Run from the repo root (kit/freya.py keeps `.freya-agents.json` relative to the working directory).
- `FREYA_AGENT_PROVIDER` / `FREYA_AGENT_MODEL` unset in our shell (the build trace records them; only organisers may set them).

Manual equivalent:
1. Freeze: `git status` clean (tracked files), all offline tests pass, commit C = the builder to submit. Note C.
2. For each hidden brief N (names as delivered, e.g. hidden-1): from the repo root, with FREYA_API_KEY set and the Claude Code login active:
   `python3 builder/build.py briefs/hidden-N.md briefs/hidden-N.openapi.json 2>&1 | tee traces/hidden-N.log`
   The trace's START line records `commit=C` (a `+dirty` suffix would mean tracked files differed from C: then do not submit that run).
3. Check: exit 0, `status=ok`, export at `submission/hidden-N.workflow.json`, `traces/hidden-N.jsonl` present;
   `.venv/bin/python devtools/secret_scan.py submission traces` prints nothing.
4. Commit only the artefacts: `git add submission/hidden-1.workflow.json submission/hidden-2.workflow.json traces/hidden-1.log traces/hidden-1.jsonl traces/hidden-2.log traces/hidden-2.jsonl`
   (optionally `git add -f out/hidden-*.policy.json`), commit S = C + artefacts. Then run
   `.venv/bin/python devtools/verify_submission.py hidden-1 hidden-2`: it must print `0 problem(s)` (clean code commit C in both traces,
   ids and export hashes match, one run per trace, only artefact paths changed between C and S, secret scan clean).
   A full dry run of steps 1-4 on the public briefs: `sh devtools/rehearse.sh <empty dir> <commit> brief-a kit/briefs/brief-a.md kit/tools/openapi.json ...`
   (evidence/rehearsal-2: 106 s and 135 s, 0 problems).
5. Push (Mert's remote), then send the form with `commit: S`, `run_command: python3 builder/build.py {brief_path} {tool_api_path}`, the two
   workflow paths and the two trace paths.
6. If a builder bug is fixed after 17:00 UTC: separate commit with a message that says what crashed and why the fix contains nothing from
   the hidden briefs; rerun both briefs from that commit so traces and code match.
