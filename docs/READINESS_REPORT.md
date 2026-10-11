# Readiness report — 2026-10-11 (checkpoint before the hidden briefs open at 20:00 Istanbul / 17:00 UTC)

Status: **NOT complete.** The submission path is rehearsed and the fallback builder is measured, but the newest candidate (C14) has no
valid practice measurement. This report separates what is verified from what is pending.

## Selected builder
- **C5** — builder tree `d0b9c09e9ba86f9a2cb9d414c626d31992eb6591` (tag `cand/C5` = f72a3e0; carried unchanged by `dev/overnight`).
  Kept because it is the strongest candidate with a complete, comparable measurement and a passing clean-checkout rehearsal.
- **C14 not promoted.** Promotion rule (docs/DAY_PLAN.md): r14 must reach at least r9 (runs 94/99, groups 27/30) on the identical suite
  with no new failure class. r14 has not produced a single valid run (below). Passing offline tests and a build rehearsal are not
  evidence of conversation quality.

## Measurements (practice customers; one simulation at a time; missing verdicts never counted as passes)
Full suite (devtools/regress.sh, 99 runs: A plain x3, B plain x3, B styled x6, A styled x3, A calendar x3, B calendar x3):

| Candidate | Ledger correct | Conversation pass / fail / missing | Run passes | Groups passing every rep | Evidence |
|---|---|---|---|---|---|
| C5 (selected) | 97 | 93 / 6 / 0 | 93 | 27/30 | evidence/regression/r5-C5 |
| C9 | 99 | 94 / 5 / 0 | 94 | 27/30 | evidence/regression/r9-C9 |
| C14 | — | — | — | — | r14 attempt 1 failed before any simulation; r14b pending (network) |

Targeted suite T1 (66 runs: 8 failure-prone A cases x 6 styles + B x 6 styles):

| Candidate | Ledger | Conversation pass / fail / missing | Run passes | Groups every rep | Status |
|---|---|---|---|---|---|
| C5 | 64 | 55 / 11 / 0 | 55 | 5/11 | valid (evidence/regression/t1-targeted) |
| C9 | 62 | 63 / 3 / 0 | 60 | 6/11 | valid (same) |
| C10 | A only | A: 42 judge-pass of 48, 3 missing | — | — | partial, outage-affected (results/t1-c10-a) |
| C12 | — | 27 verdicts missing, 7 calls never created | — | — | **invalid** (14:05 outage; results/t1-c12-a.firstcollect-outage) |
| C13 | — | — | — | — | never ran (POST 504, nothing created) |

C9 vs C5: C9 is better on T1 run passes (60 vs 55) and equal on full-suite groups (27/30); it was never promoted or rehearsed through
final_run.sh. C10-C14 contain C9's changes plus fixes (C11 partial-plan guard, C12 no repeated offers, C13 numbers in two parts, C14 review
fixes) but none of C11-C14 has a valid live measurement.

## C14 status (dev/c10)
- 947438f: 8 confirmed bugs from an independent review of C10-C13 fixed (partial-plan guard key, bare-tens date guard once per utterance,
  outcome/reason/product-id/product-type role inference on renamed APIs, extraction-guide definitions); 10 tests (7 fail on C13).
- 8cb1932: kit-audit adoptions (phone-agent model traced, fail fast without FREYA_API_KEY, 2 lints). 106 offline tests.
- Built for r14 from C10's extracted policies (`--ir`, no model): c14-a workflow 026b5c1a… / agent 807e3abd…, c14-ab workflow 661361a6… /
  agent c018aed8…, both published version 1, traces name commit 8cb1932 (clean).
- Merge candidate `cand/c14-merge` b53a4c9 (dev/overnight + dev/c10, no conflicts, builder tree identical to 8cb1932; R5/R6/devtools
  files byte-identical to dev/overnight).
- **Clean-checkout rehearsal of b53a4c9 passed** (evidence/rehearsal-c14): real extraction (2 Claude Opus calls per brief, rate limit
  'allowed'), brief-a 114 s, brief-ab 137 s, policy checks round 1, lint, self-tests, dry run, publish, read-back, export, secret scan,
  verify_submission 0 problems; IR vs reference: 0 exact differences (A: 3 text, A+B: 4 text). Limit: no conversation measured.

## Infrastructure events today
- Freya outages 12:45-13:07 and 14:05-14:13 (504). Simulation 5c60e9fa (t1-c12-a) stuck 'running' until stopped at Mert's request (16:50).
- Claude session limit for subagents ~12:20-13:40 and until 17:00; the 17:01 rehearsal confirmed the allowance is available.
- **Mock bank unreachable from this machine since ~16:51**: DNS OK (188.114.96.0/97.0), TCP 443 OK, TLS reset right after ClientHello for
  freya-bank-mock.freyavoice.workers.dev and for workers.dev; www.cloudflare.com and Freya OK; no proxy variables. Hypothesis (unconfirmed):
  network filtering of *.workers.dev. It blocks only our local runner (bank sessions, ledger reads); Freya's agents call the bank from
  Freya's side. r14b relauncher (results/r14b-launch.sh) waits for it until 18:25.
- An interrupted fallback rehearsal (archive/rehearsal-c5-20261011T140706Z, stopped by Mert after the first model call; nothing saved to
  Freya) is kept as evidence of an aborted attempt, not a result.

## Resources
- Freya: 449.29 credits at day start; 394.07 at 16:50 (verified) -> 55.2 credits (~$27.6) used today. The authorisation is unchanged;
  a full 99-run suite has used roughly 15-30 credits; keep >= 40 credits for final builds and checks (builds themselves use no simulation).
- Claude: included Team allowance only; no paid usage.

## Exact final-run commands (separate final-production stage, after 17:00 UTC)
From the repo root, on the selected builder (C5 = `dev/overnight`; if C14 is promoted later, `dev/overnight` after the merge):
```bash
cd /Users/mertcicekci/Documents/ChatGPT/freyahackathon && git checkout dev/overnight && git status --short   # tracked files clean
(cd kit && shasum -a 256 -c ../kit.sha256)          # 16 x OK; confirm kit VERSION 3 is still current on the official page
set -a && . ./env.sh && set +a                       # FREYA_API_KEY; Claude Code login active; FREYA_AGENT_* unset
sh devtools/final_run.sh --commit briefs/hidden-1.md briefs/hidden-1.openapi.json briefs/hidden-2.md briefs/hidden-2.openapi.json
```
Use the delivered file names. final_run.sh refuses a dirty code tree or failing tests (main repo .venv has pytest), archives earlier
attempts, runs `python3 builder/build.py <brief> <api> 2>&1 | tee traces/<name>.log` per brief, checks exit/status/export/trace/commit,
secret-scans, commits only the artefacts and runs verify_submission.py (must print `0 problem(s)`). It never pushes.

## Blockers and remaining actions (Mert)
1. **GitHub:** no remote exists. Create the repository, give the organisers access, push `dev/overnight` (with the artefact commit S).
2. **Form:** send before 20:00 UTC: team, repo, commit S, run_command `python3 builder/build.py {brief_path} {tool_api_path}`,
   workflows `submission/<name>.workflow.json`, traces `traces/<name>.log`.
3. **Rotate the Freya API key after the event** (it was pasted into a chat earlier).
4. Organiser questions sent by Mert (rebuild environment for top-10 reruns; close_call reason values) — answers not received here.
5. Optional, if the network allows before 18:25: r14b (C14 full suite) and, if it qualifies, merge `cand/c14-merge` and use it.

## Known limitations of the selected builder (C5)
CAL-XMAS floor wording (r5 A calendar 6/9 conversation), PUB-12 multi-product attempt counting, simulator truncation (talkative),
identity under truncated input, cut-off numbers; brief rules without any public case (deceased, wrong number, no-callback, decline,
scam, human, dispute, double recording failure) are wired in the compiled workflow but never exercised live. Hidden-brief performance
is unknowable before release.
