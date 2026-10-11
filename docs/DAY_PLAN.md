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
- 08:50 T1 targeted diagnostics on C5 started (A: PUB-02/03/04/05/06/10 + CAL-XMAS/CAL-FRI x 6 styles; B x 6 styles).
- 08:55 C9 = 3f3784b on dev/c9 (C5 + floor note, complete-date example, cut-off numbers never completed, reopen refused product), built c9-a/c9-ab; T1 on C9 queued.
- 08:58 tests/test_devtools.py tracked with SYNTHETICTEST fixtures + narrow scanner exception (3e8dc3c); devtools/final_run.sh (47daf0c); R4b-live materials (50db0a8).
- 09:05 subagents: omission audit (15 confirmed gaps), truncation study (persona-side; re-ask beats backchannels), R5/R6 adversarial briefs (committed 2ad9c08, 6570cf7). Workers W1 (API robustness, worktree ../fh-w1) and W2 (policy/prompt fixes, ../fh-w2) running.
- 09:10 T1 C5: A 39/48 runs (CAL-XMAS floor note 5/6 fail; truncation; PUB-02 asks_back silent third-party step), B 16/18. R4b-live built on C9 (slot r4b-live); queue: T1-C9 -> R4b-live -> r9 full. Extraction R5 0 diffs, R6 2 harmless; R5/R6 builds crash on role inference (W1).
- 12:30 ROOT CAUSE of vanished R5/R6 files (verified): my commit 4baeea6 on dev/c9 used `git add builder tests`, sweeping the subagents'
  in-progress untracked files (brief-r5.md draft, brief-r6.md, api-r6.json, ir-r6.ref.json) into dev/c9; the next `git checkout dev/overnight`
  removed them from the working tree (tracked on the source branch, absent on the target). No subagent deleted anything. Fix: stage explicit
  paths only; no branch switching in the main tree (worktrees). The drafts live on in dev/c9/dev/c10 lineage; when merging, the final
  versions committed on dev/overnight (2ad9c08 R5, 6570cf7 R6) must win.
  (Drafts in 4baeea6 are byte-identical to the final committed R5/R6 files - verified by sha256 - so no merge risk.)
- 12:25 r9 (C9 full) ledger 99/99, runs 94, groups 27/30 (C5 r5: 97/93/27). R4b-live (C9) 17/18 (1 platform sandbox fault). Evidence 33fa986.
- 12:30 C10 offline checks (Claude session limit until 13:40, so no model calls): R1/R2/R3/R5/R6 reference IRs compile/lint/self-test on C10;
  generated code scanned (kit + 5 variants, 30 snippets each): no control characters, all regex literals compile; lint now enforces it (7037bec).
  Goldens: W2's test compares C10 kit output with a golden taken at 4baeea6 BEFORE worker edits and allows only enumerated changes - passes.
  R6 discrepancies verified harmless in code: max_wrong_total has no reader in builder/; after_now true/false give identical callback verdicts
  for R6's "within 3 business days, never today" rule.
- 12:35 C11 = bf60bd2 (C10 + partial-plan guard, from r9/r5 PUB-12 plain diagnosis) built from C10's extracted policies via --ir (no model).
- KIT RE-ASSESSMENT (12:35): /Users/mertcicekci/Downloads/build-the-builder 2/ is identical to repo kit/ (VERSION 3, diff -rq empty), incl.
  the hidden .claude/skills/build-phone-agent/SKILL.md (not read before today). The skill drives kit/examples/agent_builder.py: an agentic
  Claude session hand-writes workflow JSON from base-agent.json, saves it, tests on practice customers (run_cases.py --brief A/B) and fixes
  up to 3 times. Decision: not used in our submission path. Reasons: (1) hidden briefs have no practice customers, so its test-fix loop - its
  main quality lever - cannot run in the final; (2) our compiler + verified template already implements the skill's rules (route on tool
  results, rules in routes/code rather than prompts, never claim a failed recording); (3) measured: kit base agent 0/13 vs our C5/C9 93-94/99.
  Relevance: ~150 competitors likely start from this baseline. A head-to-head run is low priority (long agentic run + sims, no change to what we ship).
- 12:45-13:00 FREYA OUTAGE: GET/POST /api/v2/simulations and /workspaces return 504. T1-C10 incomplete: A part lost polling of simulation
  3f90e97a-456a-4d52-826f-ca64bd658f16 (may have completed on the platform; to collect read-only); B part POST /simulations -> 504
  (unknown whether created; to check via GET /api/v2/simulations list before any launch). T1-C11 launcher stopped before it created anything
  (empty result dir set aside as results/t1-c11-a.aborted-*). Owned launchers: none running; recovery check: 1 GET / 2 min.
- 12:58 C12 = 61dc7a4 on dev/c10 (C11 + after answering a question never repeat an offer; negotiation fragments get "could you say that
  again?" instead of a restated offer) from r9 PUB-06 diagnosis. Not built yet (Freya down).
- Next after recovery: reconcile -> T1 on C12 (identical suite) -> full r12 if T1 >= C9 (60/66) -> repeat for variance -> select ->
  rehearsal from clean checkout -> live auto-builds of R5/R6 (after Claude limit reset 13:40) -> docs.
- 13:3x T1-C12 (C12 slots built from C10's extracted policies via --ir) started; C13 slots built the same way, T1-C13 queued.
- 14:05-14:13 SECOND FREYA OUTAGE (504 on simulations/workspaces/credits). T1-C12 A lost polling (launcher scored it with 27 missing
  verdicts / 7 'no call created' - not a measurement); T1-C12 B and T1-C13 POSTs -> 504 (nothing created: GET /simulations shows no
  t1-c12-b / t1-c13 simulation). Simulation 5c60e9fa (t1-c12-a) stayed 'running' (platform: 41 pass, 1 fail, 4 inconclusive, 2 stuck
  analyzing), blocking the workspace's single simulation slot. Ending it via PUT status=completed was denied by the auto-mode classifier;
  Mert ends it himself (16:37). Credits 16:35: 394.07 (day start 449.29 -> 55.2 credits / $27.6 spent today).
- 14:10-16:30 session interrupted (Claude session limit for subagents until 17:00; audit critic failed on it).
- 16:30 C14 = 947438f (independent review of C10-C13: 8 confirmed bugs fixed, 10 tests) + 8cb1932 (kit-audit adoptions: trace the
  phone-agent model, fail fast without FREYA_API_KEY, 2 lints). Roles unchanged on kit + 5 variant APIs; kit golden diff = 2 hook edits.
  Built c14-a / c14-ab from C10's policies (--ir, no model). r14 (full 99-run suite, frozen copy) waits for the slot (results/r14-launch.sh,
  gives up at 18:25 so it cannot cross 19:30).
- 16:40 docs/RESOURCE_AUDIT.md (aee8fbb): 16/16 kit files + paper read in full; decisions; gaps. Merge dev/c10 -> dev/overnight dry run:
  no conflicts, 106 tests pass, dev/overnight's R5/R6/devtools files byte-identical after merge (detached worktree ../fh-merge-check, 2b21ed0).
- Promotion rule: promote C14 only if r14 >= r9 (C9: runs 94, groups 27/30) without a new failure class; otherwise keep C5.
- 16:50 Mert asked me to stop 5c60e9fa: PUT status=completed -> 'completed' (first collection kept as results/t1-c12-a.firstcollect-outage).
- 16:51 r14 attempt 1 FAILED BEFORE CREATING ANYTHING: every part died at the mock-bank session call (devtools/bank.py session ->
  URLError Errno 54 'Connection reset by peer'); credits unchanged (394.0695 before/after); logs kept as results/r14-*.log.
- 16:52-16:57 connectivity (bounded, one request each): DNS freya-bank-mock.freyavoice.workers.dev -> 188.114.96.0/97.0 OK; TCP 443 OK;
  TLS reset right after ClientHello (curl 35 / LibreSSL errno 54) for the bank AND for https://workers.dev/; https://www.cloudflare.com/
  TLS + HTTP 200 OK; Freya staging-app OK; no proxy variables. The bank worked from this machine at 13:34. Hypothesis (unconfirmed):
  SNI filtering of *.workers.dev on the current network. Asked Mert to change network/VPN. Relauncher results/r14b-launch.sh waits
  (1 request / 120 s), then re-collects t1-c12-a read-only and runs the identical frozen suite as r14b; gives up at 18:25.
- Suite duration (log times): r5 24 min, r9 21 min -> a launch at 18:25 ends ~18:50, inside the 19:30 stop.
