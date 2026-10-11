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

## Correction and version 3 (22:29-22:36 Istanbul) - SUBMITTED: artefact commit 8250bd4
- Correction: the 22:24 note above ("the change was never written") is wrong, and so is the same phrase in commit e4619fd's message.
  The independent review found week_change WAS called with success in run3 EX2 (the event is labelled with the tool name, which my
  filter skipped). The real defect: after a successful write the agent called the exit tool in the same message and the success
  sentence was never spoken (also run2 EX4). The guard (e4619fd) addresses exactly that ordering; live results below confirm it.
- Version 3 build: official command through final_run.sh, code commit e4619fd, artefact commit 8250bd4. The construction model output
  is REPLAYED (builder replay backend) from the recorded version-2 Claude Code session df49457b - accepted only because the prompt
  the builder sends now is byte-identical to the recorded one (devtools/replay_from_session.py) - then the general post-processing
  (field defaults + exit-ordering guard) is applied automatically. The full session log is printed in traces/final.log. Our own
  verify_submission flags "no successful model call in the trace" for this reason: the model call is the recorded one.
- Live (agent version 3): run4 (sim fab3d2f0) 11/12; run5 targeted repeat (sim 63c6148b) 8/10 -> 19/22.
  EX2 3/3 (v2 0/2), EX4 3/3 (v2 1/2), T8 3/3, T6a 3/3, EX5 0/3 (v2 0/2: "evet + spouse's card" is taken by the router's
  third-party exit before the change - not covered by this repair). All other scenarios passed in run4.
