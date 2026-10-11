# Morning report — overnight run 2026-10-11 (DRAFT, finalised at the end of the run)

Window: started 03:43 Istanbul; deadline 09:43, moved by Mert at 05:26 to **11:00 Istanbul (08:00 UTC)**.
Branch `dev/overnight` (local only, no remote). Baseline `a0e7f68` (tag `baseline-v4`, = `master`) untouched.

## Status at a glance
- Automatic extraction: done and verified (see Extraction). The submitted path uses no replay or hand-written policy.
- Same-version regression: C2 fully measured (r2); C3 partially (r3, judge verdicts missing); **C4: r4 — PENDING (filled in below)**.
- Clean-checkout rehearsal: preliminary one passed (135 s, A+B); **final rehearsal on the best candidate — PENDING**.

## Candidates
| Name | Builder commit | Freya | Measured |
|---|---|---|---|
| baseline | a0e7f68 | dev-a v4 / dev-ab v4 (hand-written IR) | v3: A 20/20 runs; v4: B plain 3/3, styled 3/6 |
| C1 | e774ccd | cand-ab v1 | B asks_back/talkative x6: ledger 18/18, runs 16/18 |
| C2 (tag cand/C2) | 82c7498 | cand-a v1, cand-ab v2 | r2 (99 runs): ledger 93, conversation 85, runs 84, case groups all-reps 20/30 |
| C3 | 603b2a8 | cand-a v2, cand-ab v3 | r3 A plain only: ledger 29/30, 15/30 verdicts missing (pending) |
| C4 | bd59251 | cand-a v3, cand-ab v4 | r4 PENDING |

## Extraction (genuine model calls, Agent SDK on the Team login)
All runs: 2 model calls (extract + audit), policy checks pass in round 1, 95–185 s per build.
| Brief | Runs | Exact-field differences vs hand-written reference |
|---|---|---|
| Public A | 4 (x-a, cand-a x3) | 0 each |
| Public A+B | 6 (x-ab, x2-ab, cand-ab x3, rehearsal) | 0 each |
| R1 raw material (memo supersedes 7 rules, outcome code list, mutated API) | 2 | 0 each |
| R2 SME lender (written by an independent subagent; extra call variable, Saturday business days, 0-attempt band, no paid outcome) | 1 | 1 (callback.after_now; harmless with "never today") |
| R3 controlled mutations of A (calendar-day deadlines, MDY dates, 3 bands / 3-step ladder, holidays, renamed outcome) | 1 | 0 |
Evidence: evidence/extraction-1, -2, -3; rule-level review by a subagent (findings applied in ed00815).

## Usage
Builder model calls traced so far: 30, 0 failed, ~232k output tokens (API list-price estimate ~$7.3, not billed: Team allowance).
Rate-limit state from the SDK: "allowed", five-hour window (reset 07:20 UTC). No interruption so far. Subagent/workflow usage is
part of this Claude Code session and not itemised.

## Submission blockers (Mert)
- Create the GitHub repository, give the organisers access, push `dev/overnight` (or merge to master). No remote exists.
- Decide on tests/test_devtools.py (untracked; fake key strings trip the secret scan).
- Procedure: docs/SUBMISSION.md; checker: devtools/verify_submission.py.
