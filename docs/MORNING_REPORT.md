# Morning report — overnight run 2026-10-11

Window: started 03:43 Istanbul. Deadline moved by Mert at 05:26 from 09:43 to **11:00 Istanbul (08:00 UTC)**; work finished ~08:15 Istanbul.
Branch `dev/overnight` (local only, no remote). Baseline `a0e7f68` (tag `baseline-v4`, = `master`) untouched; its Freya workflows dev-a / dev-ab
and their exports (checkpoints/baseline-v4/) unchanged.

## Bottom line
- **Selected candidate: C5.** Builder code = `f72a3e0` (tag `cand/C5`); `dev/overnight` HEAD carries exactly that builder again
  (restore commit `5d721a2`, after C6/C7 measured lower). Offline tests: 50 pass.
- **Clean-checkout rehearsal of the selected commit passed** (evidence/rehearsal-3, code commit 5d721a2): brief-a 110 s, brief-ab 147 s,
  `verify_submission.py` 0 problems. (Also rehearsal-2 on b9073e3: 106 s / 135 s.)
- Completion criteria met: genuine automatic extraction (29 extraction runs), same-version regressions (C2, C4, C5, C6, C7: 99 runs each),
  clean-run rehearsal. Not "proven to win": known failure classes remain (below); hidden briefs cannot be tested before 17:00 UTC.

## Measured results (practice cases; one Freya simulation at a time; never mixed across versions)
Suite per candidate (`devtools/regress.sh`, frozen copy per run): A plain x3 (10 customers), B plain x3 (3), B asks_back/talkative x6,
A asks_back/talkative/in_words x3, A calendar cases x3 (Friday roll-over, holiday "next Monday", Christmas Eve/year end), B calendar x3. 99 runs.

| Candidate | Builder | Ledger correct | Conversation pass / fail / missing | Run passes | Case groups all reps |
|---|---|---|---|---|---|
| C2 (cand/C2) | 82c7498 | 93 | 85 / 14 / 0 | 84 | 20/30 |
| C3 | 603b2a8 | A plain only 29/30 | 13 / 2 / **15 missing** | — | — |
| C4 (cand/C4) | bd59251 | 97 | 93 / 6 / 0 | 92 | 23/30 |
| **C5 (cand/C5)** | **f72a3e0** | **97** | **93 / 6 / 0** | **93** | **27/30** |
| C6 (cand/C6) | 6b709f5 | 96 | 92 / 5 / 2 missing | 92 | 23/30 |
| C7 (cand/C7) | 5ff498a | 95 | 85 / 7 / 7 missing | 85 | 18/30 |
Missing verdicts = the platform judge produced none (re-collected read-only; still missing) — pending, never counted as passes.
C5 by part: A plain 30/30 (10/10), A styled 30/30 (10/10), B plain 8/9, B styled 16/18, A calendar 6/9 (ledger 9/9), B calendar 3/3.
C7 not promoted: new failure classes in A talkative/asks_back (PUB-03 verify turn limit -> declined, consistent with its "never re-ask on
small talk" rule stalling; PUB-05 refused instead of hardship; PUB-02 no callback). C6 fixed the calendar floor note (A calendar 9/9) but
its totals were lower and it recorded one promise after three wrong DOB answers (PUB-03 r3 in_words) — not promoted.
Evidence: evidence/regression/r2-C2, r3-C3, r4-C4, r5-C5, r6-C6, r7-C7 (summaries with ledgers, reports, exports and build traces).

### Live raw-material test (R4-live, C6 builder)
tests/variants/brief-r4-live.md (mail + policy + superseding memo + reason-code list) built automatically, run live 6 cases x 3:
conversation 18/18; all 6 promise runs used the memo's values (late deadline 21 Oct, 26 Oct no longer a holiday). The 12 non-promise
runs were rejected by the hosted mock because it accepts only the public brief's reason names — a limit of this test design on the
kit's mock, not of the builder (it sent the brief's codes). evidence/live/r4live-C6.

### Remaining failures and causes (selected candidate C5)
- CAL-XMAS 3/3 conversation: floor offered as attempt 1 without "the rest stays overdue" (fixed in C6's sentence, but C6 not promoted).
- PUB-13 talkative 2/6: simulator truncation — customer turns are cut before the answer; seen in every build.
- PUB-12 plain 1/9 (judge): a mislabelled refusal led to a repeated car-loan offer (known trade-off of the 'today' guard).
- Identity under truncation: a complete wrong answer followed by cut-off words may get "Go on." (C6 r6 PUB-03 r3; C7 tried a fix but regressed elsewhere).
- Cut-off numbers: "I accept the twenty" was completed as the 20th (C6 r6 PUB-04 r3). A fix is on branch `exp/c8-truncated-day`
  (commit 4574196, on top of C7 — unbuilt, unmeasured).
- Platform: one call with every code execution failing (C4 r4 PUB-05 r3); judge outages (r3: 15, r7: 7, r6: 2 missing verdicts).

## Extraction (genuine model calls; Agent SDK on the Claude Code login)
29 automatic extractions (58 traced model calls), each 2 model calls (extract + audit), policy checks pass in round 1, 95–185 s per build.
| Brief | Runs | Exact-field differences vs hand-written reference |
|---|---|---|
| Public A | 10 | 0 each |
| Public A+B | 13 | 0 each of the 12 diffed (the C1 build at 03:54 was not diffed) |
| R1 raw material (memo supersedes 7 rules, outcome code list, mutated API) | 2 | 0 each |
| R2 SME lender (independent author; extra call variable, Saturday business days, 0-attempt band) | 1 | 1 harmless |
| R3 controlled mutations (calendar-day deadlines, MDY dates, 3 bands, holidays, renamed outcome) | 1 | 0 |
| R4 raw material on the kit API (mail, memo, reason codes) | 2 | 2 defensible (brief silent on day/month swap and on one-product limit) |
Evidence: evidence/extraction-1 … -4, rehearsals.

## Compliance
Every model-call attempt traced (incl. failures, timeouts, skips, CLI-internal Haiku requests, rate-limit state); 29-minute whole-process
watchdog from first start (survives re-exec); runtime bootstrap from the lock files; one run per trace; "+dirty" over code paths only;
final traces/exports committable (.gitignore fixed); docs/SUBMISSION.md quotes the official rules (one commit + exports + per-brief
traces; no history requirement) and the procedure; devtools/verify_submission.py checks the artefact commit against its code commit.

## Usage and access
Builder model calls traced: 58, 0 failed, ~446k output tokens (API list-price estimate ~$13; not billed, included Team allowance).
Rate-limit status "allowed" throughout; no usage interruption. After the ~06:50 session restart the Claude Code login (used by the
builder) is thomas@getmotif.dev on the GetMotif Team plan; before that the same Team org. Commits keep the existing author identity.
Workspace: 17 of 200 workflows (rehearsals in fresh clones create new "brief-a"/"brief-ab" workflows each time).

## Artefacts
- Selected candidate exports/traces: evidence/regression/r5-C5 (c5-a / c5-ab; Freya c5-a 2f50d664…/agent 66b8c003…,
  c5-ab e19f3615…/agent 369c3762…) and evidence/rehearsal-3 (rehearsal workflows 8b0d40be…, a45ca211…).
- Tags: baseline-v4, cand/C2, cand/C4, cand/C5 (selected), cand/C6, cand/C7 (measured, not promoted). Log: docs/OVERNIGHT_PLAN.md.
- results/ (gitignored) keeps raw runs; results/r7-artifacts = never-measured C7 v1; results/r7-artifacts-c7v2-*/ = what r7 measured.

## Submission blockers (Mert)
1. Create the GitHub repository, give the organisers access, push `dev/overnight` (or merge to master). No remote exists; nothing was pushed.
2. tests/test_devtools.py stays untracked (listed in .git/info/exclude): it contains fake key-shaped strings that the pre-commit secret
   scanner correctly blocks, and rewriting them was refused by the auto-mode classifier. The scanner was not disabled or bypassed.
   Options: rewrite the fixtures by hand so no key-shaped literal appears, or keep the file out of the repository.
3. Sunday 17:00 UTC: follow docs/SUBMISSION.md (freeze C, run both briefs with tee, commit artefacts as S, verify_submission.py, send the form).

## Next actions (priority)
1. Decide whether to accept C5 as the submission builder or spend daytime practice runs on a C5-based variant with the calendar floor
   sentence (C6's) and the cut-off-number rule (exp/c8) — only with a full 99-run comparison; judge outages make small differences noisy.
2. Optional practice with pieces/corrects on brief A (covered only on B calendar overnight).
3. Known template limits: non-English calls (template strings are English), customer-requested callbacks after verification,
   rules outside the policy schema reach the agent only as rule text.
