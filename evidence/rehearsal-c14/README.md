# Clean-checkout rehearsal of C14 merge candidate (2026-10-11 17:01-17:05 Istanbul)

Command: `sh devtools/rehearse.sh archive/rehearsal-c14-20261011T140102Z cand/c14-merge brief-a kit/briefs/brief-a.md kit/tools/openapi.json brief-ab out/brief-ab.md kit/tools/openapi.json`
(out/brief-ab.md = brief-a.md + brief-b.md, sha256 72b3508b…, verified). Uses the clone's devtools/final_run.sh --commit.

- Code commit C = b53a4c9 (cand/c14-merge; builder tree f956041a… = dev/c10 8cb1932, the revision built as c14-a/c14-ab for r14).
- Fresh clone, no .venv: runtime bootstrap built .venv from uv.lock (CPython 3.12.6, claude-agent-sdk 0.2.165) and re-executed.
- REAL model extraction: 2 Claude Opus 5.5 calls per brief (extract + audit), status success, rate-limit status 'allowed' (Claude
  allowance available at 17:01). Policy checks pass in round 1; lint pass; 3 local self-tests pass; platform dry run valid;
  save + publish; read-back every node/tool stored; published version newest; export written.
- brief-a 114 s, brief-ab 137 s (30-minute limit). Phone-agent model traced: experimental-5/experimental-5, not overridden.
- Secret scan clean (final_run and again on this folder). Artefact commit S = 209820d in the clone; verify_submission.py: 0 problems.
- Extraction vs hand-written reference (devtools/ir_diff.py, same method as evidence/rehearsal-3): brief-a 0 exact / 3 text differences;
  brief-ab 0 exact / 4 text differences (wording only, e.g. "I am" vs "I'm").
- Rehearsal workflows: brief-a 14fd9a92… (agent da88f1bf…), brief-ab f5d7f40b… (agent eec03267…).

Limits:
- final_run.sh runs the offline-test gate only when .venv exists; in a fresh clone it does not exist yet, so the gate was skipped
  inside the rehearsal. Checked separately: the clone's tree passes 106 tests (main repo .venv has pytest 9.1.1 from the dev group;
  the lock-built runtime .venv has no pytest by design). In the real final run in the main repo, .venv exists and the gate runs.
- A build rehearsal proves the submission path (dependencies, auth, extraction, compile, publish, export, traces, checks). It does NOT
  prove live conversation performance: no simulation and no mock-bank access were involved.
