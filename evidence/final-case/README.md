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
