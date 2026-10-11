# Clean-checkout rehearsals of the frozen final candidate ef5f718 (2026-10-11 18:34-18:40 Istanbul)

Each: `sh devtools/rehearse.sh <new dir> ef5f718 final-<n> <zip> <zip>` - fresh clone, test gate (uv sync --frozen incl. dev group):
143 passed; real extraction (2 Claude Opus calls); policy checks round 1; lint; 3 local self-tests; dry run valid; save + publish;
read-back; newest published version; export; secret scan; artefact commit; verify_submission 0 problems. No simulation.

| Zip | Content | Wall | Status | IR vs reference |
|---|---|---|---|---|
| kit | kit briefs A+B as documents + kit contract | 174 s | ok | 0 exact / 3 text |
| r5 | our independent R5 case (raw material, memos, code list with retired codes) + its renamed-field contract | 181 s | ok with warnings | 0 exact / 2 text |

R5 warnings (by design, for the operator): contract outcome 'CBK' and reasons 'WEL', 'SYS' are never produced - in R5's code list
they are retired/unused codes, so leaving them out is correct (the model also correctly left outcomes.by_reason empty: callback booked
is NOCON/CB, active); three rules the IR cannot express are traced (S1 deadline rolled back to the previous business day; card floor =
full amount at or below £30; 24/31 Dec callback cut-off 14:00) - they reach the agent as rule text only.
Limits: no conversation measured (practice bank unreachable from this network; no hosted mock).
