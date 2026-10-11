# Strategy and architecture proposal (for discussion, not yet implemented)

Status 2026-10-10 ~23:00 Istanbul. Labels: **[V]** verified by us, **[S]** stated by organisers (kit/page/kickoff transcript), **[I]** inference, **[T]** must be tested.

## 1. What is judged
- Builder run as `python3 builder/build.py {brief_path} {tool_api_path}`; one Markdown brief + its own OpenAPI per run; 30 min max incl. own tests [S page].
- Score = cases passed / 60 (2 hidden briefs x 30 customers, 1-3 products, several calendar days, 3 runs each, all 3 must pass) [S]. A case passes only if ledger matches exactly AND the judge (persona/analysis model gemini-3-flash-preview in practice) passes the case checks [S kit].
- Ties: promised amount on passed cases, then total build time (from traces), then submission time [S].
- Top 10: organisers clone the commit, re-run the builder, compare with the trace; a reviewer agent reads every trace; "If our run scores clearly below yours, the entry is removed" [S page]. => builder variance is a disqualification risk, not just noise.
- Rules: phone agent on fixed `experimental-5`; may call only the kit's tool API ("a call to any other service fails the case"); no hidden-brief content in builder code; no hand edits; "Do not probe or load-test the platform" [S].

## 2. Core strategic consequence
Hidden briefs have no practice customers [S FAQ], so the example builder's main quality lever (test on practice cases, fix, retest) does not exist in the final. Quality must be produced **at development time** and carried into the final by a builder that is correct by construction and low-variance:
- a deterministic compiler + a phone-agent template we harden today on live simulations,
- LLM calls only where language understanding is required (reading the brief into a typed policy), each verified.

## 3. Proposed builder (smallest credible version)
1. **Ingest (code):** parse the OpenAPI into tool contracts (server URL, paths, request fields, enums, required, response fields). Never typed by an LLM.
2. **Extract (LLM, structured output):** brief -> Policy IR (identity questions, calendar, bands/deadlines, floors, ladder, callback window, outcome table, notes, transfers, verbatim required sentences, free-text rules per stage) with a verbatim quote per value and a precedence log for memos/amendments.
3. **Verify (code + LLM):** quotes must occur verbatim in the brief; every enum/field referenced must exist in the OpenAPI; outcome table complete; omission pass ("which normative sentences are not represented?"); disagreements between two independent extractions adjudicated against the source.
4. **Compile (code):** IR -> workflow from a tested template: code nodes for dates/amounts/ladder/outcome fold; engine-run writes (get_account, create_promise loop, notes at non-transfer wrap-up, transfer, callback); normalising pre-hooks; post-hooks that tell the model the next step; lifecycle `close_call` with outcome computed from state.
5. **Validate (code):** local lint; run the generated code-node Python locally on scenarios derived from the IR; platform dry run; save; read-back diff; published version check; chat smoke turns when the platform allows.
6. **Export + trace:** start/end, every model call (model, one-line purpose, tokens, cost), workflow/agent ids, node/tool counts, tests and results, input/output hashes.

Estimated time per run: 5-10 min. Model access: Claude Agent SDK on the runner's own Claude Code credential (Mert: Team subscription); no API key required. See docs/MODEL_ACCESS.md. (At API list prices the same 5 calls would be ~$1.3/run; only relevant if someone chooses an API key.)

## 4. Alternatives to measure against
- A0 kit base agent (fixed JSON) - measured baseline.
- A1 kit example builder output (agentic, Claude writes the whole workflow) - typical competitor.
- A2 our compiled template (above).
- A2 variants: interaction style inside negotiation (model-called code tool vs extraction + code node), temperature setting (if allowed).

## 5. Test strategy (development time)
- Hosted fixtures PUB-01..13 with **our own scripts and expectations** (the bank keys data by case id; persona text and scoring are local) -> B-variants of A cases, failure paths, transfers after promises, mixed notes.
- `now` overrides -> other calendar days (Friday, before holidays, year end).
- Speech styles x 3 runs packed into one simulation (`devtools/run_cases_ext.py`).
- Rule-mutation briefs (changed numbers/calendar/outcome mapping, raw-material format with memo + code list) -> extraction accuracy vs known ground truth; numeric-rule mutations can also be run live on the fixtures.
- Measure pass^3 per case, not single-run pass rate.

## 6. Open organiser questions
See the final section of the session summary.


## Implementation status (2026-10-11 ~00:45 Istanbul)
Implemented and tested offline (24 tests): OpenAPI contracts + role inference (also on a mutated API with renamed fields/enums/$ref),
policy IR schema + deterministic checks (enums vs API, product fields, band coverage, verbatim quotes, holidays mentioned),
runtime (calendar, deadlines, ladder, callback window, spoken-day resolver, answer normalisers, outcome fold; reproduces all 13
public expected promises), compiler, lint (structure + API contract + code compiles), local execution of every generated snippet,
build orchestrator with trace, extraction pipeline (extract -> audit -> checks -> bounded repair) via a replaceable model boundary.

Live (practice simulations, hand-written public policy; not yet the model extraction):
- v1: Brief A 9/10 (base agent baseline 0/13). B 1/3 (state race found).
- v2: A 18/20 runs (2 styles). B failures traced to (a) parallel tool calls racing on shared variables, (b) no engine retry
  on model-called writes, (c) model not following the persuasion ladder.
- v3: promises recorded by an engine loop after one confirmation (save_promises oracle validates the plan; nothing is sent
  by the model), ladder enforced by a record_refusal oracle, dates by a check_date oracle. Results pending.

Agent design principles now verified: decisions that are data-decidable live in code; the model gets them through
"oracle" tools whose pre-hook always raises with the answer (no request sent); writes run as engine nodes in sequence.
