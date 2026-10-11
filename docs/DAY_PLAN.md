# Day plan — readiness before the hidden briefs (2026-10-11)

**Started:** 08:46 Europe/Istanbul. **Hidden briefs open:** 20:00 Istanbul (17:00 UTC) — this goal's deadline (replaces the 11:00 one).
**19:30:** stop starting experiments or long suites; verify and checkpoint. **20:00:** launch nothing new, stop owned launchers, report.
Do not process hidden briefs under this goal.

## Baseline (verified 08:46)
- `dev/overnight` at f948f81; builder byte-identical to C5 (f72a3e0, tag cand/C5, restore commit 5d721a2); 50 offline tests pass;
  evidence/rehearsal-3 verify: 0 problems. C6/C7 regressed (tags cand/C6, cand/C7); exp/c8-truncated-day unmeasured (on C7).
- Claude: claude.ai Team login (GetMotif), included allowance only.
- Freya credits (documented GET /api/v2/workspaces/{id}/credits): 449.29 credits left, creditUsdRate 0.5 (≈ $224.64). Authorised to use up to
  $224 on useful testing; keep a reserve for final builds/essential checks. Consumption measured as balance difference per suite.
- Freya model: workflow + backing agent are created together (kit/docs/api.md:54,67); c5-a/c5-ab: registry agent = workflow.agentId =
  export agent_id = simulation agent; runs used published version 1. No migration needed.

## Method
Targeted diagnostics first (failure classes x many styles/reps), then comparable full regressions run back-to-back (C5 and the new
candidate in the same time window to control for platform drift/judge outages), then clean-checkout rehearsal. Freeze candidate
revisions (separate Freya slots, frozen scripts). Missing verdicts stay pending. Reject regressions; keep C5 otherwise.

## Backlog (priority by scoring impact)
1. Floor offers without the required "rest stays overdue" wording (CAL-XMAS 3/3 on C5).
2. Truncated input: complete identity answer + cut-off words ("Go on" instead of verifying); cut-off numbers completed by the agent
   ("the twenty" -> 20th); never invent digits or treat incomplete input as consent.
3. Multi-product: product closed as refused, customer later names a date for all -> reopen; mislabelled refusals.
4. Calendar boundaries beyond the current 4 cases; amounts boundaries.
5. Generality: new independent synthetic briefs (conflicting memos, renamed fields/codes, boundaries) + omission audit.
6. tests/test_devtools.py: safe synthetic fixtures / narrow scanner exception, real-secret detection retained.
7. Final-run commands and local deliverables; clean-clone audit.

## Log
- 08:46 baseline verified (above).
