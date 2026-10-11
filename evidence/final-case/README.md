# Final case: what was built, tested and submitted (2026-10-11, Istanbul time)

- Case snapshot: /Users/mertcicekci/Documents/ChatGPT/final-case-2 = "Build the Builder - Final Case 2" download (19 files; SOURCES.md).
- Mock: https://merth-bank-mock.mertcicekci29.workers.dev (mock/, deployed 21:23, version 424aa0d2; MOCK_REVIEW.md). Freya reached it:
  every live call below shows api_call results from it (status 200, pack data).
- Requirements: requirements.md (independent reconciliation of all sources, precedence, 71 requirements, scenarios).

## Builds (official command `python3 builder/build.py {case_folder} {mock_url}` via devtools/final_run.sh)
| Candidate | Code commit | Construction call | Result | Live tests (Freya simulations, 12 scenarios from tests/final_scenarios.json) |
|---|---|---|---|---|
| 1 | fe3da60 | 947 s, 113k output tokens | agent built, published, exported (status=ok); trace print then crashed (BlockingIOError, fixed in d754525); not committed as artefacts | run1 (sim 2889f428): 8/12 pass. Root cause of 4 failures: model-filled api_call fields without a {{field}} default reached the mock empty -> builder fix 17c843c |
| **2 (submitted)** | **17c843c** | 1055 s, 125k output tokens | status=ok, dry run valid, read-back pass, published version 2, exported; artefact commit **e44fd75**, verify 0 problems | run2 (sim 893bf97e): 9/12; run3 (sim b6be38b2): 10/12 -> **19/24** |

Failures of candidate 2: EX2 x2 and EX5 x2 (statement-week change: after the caller's confirmation the agent did not always call the
change or report its result), EX4 x1 (sent e-mail not announced before the goodbye; passed in the repeat). Judge verdicts are the
platform's simulated-customer analysis on our scenarios, not the organisers' hidden score.

## Final checks (22:18-22:24 Istanbul) - submitted package unchanged
- Package hashes (sha256): submission.json e3217447..., submission/final.workflow.json fb37fdb4..., traces/final.log 4e0fa3f5...
- Decision: version 2 kept. A further build takes 947-1055 s plus test gate and live validation; from 22:18 it could not be
  validated before the 22:40 freeze.
- Inputs of the submitted run (traces/final.jsonl): all 19 files listed; 17 case documents read in full (incl. the 10 Oct follow-up
  transcript and the call-flow PDF via PDFKit); test-customers.json entered as its first 40,000 of 706,815 chars (its full table is in
  test-customers.md, read in full); the organisers' example mock code (worker.js, wrangler.toml) not read as text (not agent policy).
- Live Drive folder, nested listing via embeddedfolderview (22:20): exactly the same 19 file names in the same folders as the local
  snapshot; latest modification 2026-10-10 follow-up transcript at 10:54 (Drive display time), all others 10:09-10:28. Contents not
  byte-compared (no download access); website: no announcements.
- Week-change failure (EX2/EX5, run3 transcript): the caller confirmed and said "nothing else" in one turn; the end-of-call intent
  transition fired in routing before the dialogue model called statement.week.change, so the change was never written and the agent
  said goodbye. General cause: intent-based exit transitions can preempt a confirmed write; fix needs construction guidance + rebuild.
- Mock liveness through Freya at 22:23: simulation a207c8af (agent version 2), EX1 pass; the mock answered cards.list,
  contact.email.get and cards.balance with HTTP 200 and pack data. Freya credits 393.10 at 22:13.
