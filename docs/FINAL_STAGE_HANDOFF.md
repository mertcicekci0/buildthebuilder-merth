# Final-stage handoff (readiness checkpoint, 2026-10-11 Istanbul)

Readiness is **not complete**: the final case is unreleased, and the rules for the package, URLs, hosting and mock are unknown until the
17:00 UTC mail. This document is the starting point for the separate final-production stage. Branch `prep/final` (worktree
`../fh-final-prep`) carries it. `dev/overnight` stays the preserved, measured C5 baseline.

## 1. What the organiser update (17:24 Istanbul) changes
Source: organiser reply relayed by Mert (docs/DAY_PLAN.md). Nothing beyond its wording is inferred.

| Point | Before | Now |
|---|---|---|
| Final input | two hidden briefs (markdown + OpenAPI each) | **one case**: a zip with the bank's documents and its own service contract |
| Organiser rebuild of our commit | top-10 rerun with our builder | **superseded**: "We will not run your builder"; no model access needed |
| What they take | export + trace per brief, repo/commit form | **the builder's trace + the exported workflow JSON of the final agent**, tested in their workspace |
| Bank | hosted practice bank | **not used**: "You write your own mock of the bank's services from the case's contract" |
| Outcome / reason values | brief/API | "come from that case" |

Still provisional (not addressed by the update; confirm in the mail): the deadline (20:00 UTC), whether repo, commit and form fields
are still required, trace content rules ("Remove API keys"), "nothing from the final case written into the builder" after release,
whether manual input conversion (e.g. PDF to text) is allowed, and whether the mock itself is submitted or graded.

## 2. Selected preparation candidate and its limits
- **FROZEN FINAL CANDIDATE: `prep/final` at ef5f718** (tag `cand/C14-final`). Since bf732d7 (18:20-18:40): contract auth headers
  (`--tool-headers`), path parameters, YAML contracts, PDF text (PDFKit), contract warnings ('ok with warnings' + trace) for shapes that
  would fail silently, close reasons checked against the contract enum, any 2xx response, `outcomes.by_reason` (a situation with its own
  close outcome), ISO date-time callbacks, no-floor products accept only the full amount, required close reason never dropped.
  143 tests. Rehearsals of ef5f718 (evidence/rehearsal-ef5f718): kit zip 174 s, 0 exact IR diffs; independent R5 case 181 s, 0 exact IR
  diffs, warnings correctly flag retired codes and 3 rules the IR cannot express.
- Earlier: `prep/final` at bf732d7. Builder = C14 (dev/c10 8cb1932, tree f956041a) + final-shape changes
  (072e517: bundle inputs, explicit --tool-map, test-only base URL, URL checks). Compiled output for a single text brief is unchanged:
  the kit golden test passes. 127 offline tests pass.
- Why C14 and not C5: for an unknown contract, the review measured 5 contract variants. C14 compiled all 5; C5 got KeyErrors on 3 (a
  'disposition' outcome field, a nested products array, renamed product fields), failed lint on 1, and on 1 compiled cleanly but reads
  fixed verify-response names, so it would fail silently at runtime. Both share the hard limits listed in section 3.
- An independent adversarial review of the new code found 4 major and about 20 minor defects; all were fixed with tests
  (afecfd9 inputs/tool map, c8cbd36 shell, 8dcea8d mock, bf732d7 rehearse.sh refuses an existing directory). Shell behaviour was
  stub-tested under dash in a throwaway worktree. 134 offline tests pass.
- **End-to-end rehearsal of bf732d7** (evidence/rehearsal-final-shape-bf732d7): fresh clone, test gate 134 passed, one zip in the
  final's shape, real extraction, 147 s, verify 0 problems, 0 exact IR differences.
- Evidence of the submission path: clean-checkout rehearsals of C14 (evidence/rehearsal-c14: brief-a 114 s, brief-ab 137 s) and of
  prep/final in the final's shape (evidence/rehearsal-final-shape: one zip with documents + contract, 146 s, test gate ran in the fresh
  clone, verify 0 problems, 0 exact IR differences).
- **Not shown for any candidate: conversation quality on the final case.** Old-bank practice results: C5 r5 93/99 runs, 27/30 groups;
  C9 r9 94/99, 27/30 (C14 contains C9's changes). C10 to C14 have no valid full-suite measurement: the r14 attempt failed before
  creating anything, and the mock bank is unreachable from this network (TLS reset; network filtering is unconfirmed). That evidence
  concerns the old bank, not the new contract.
- Fallback: `dev/overnight` (C5) if the final contract keeps the kit's field names and prep/final shows a defect.

## 3. Assumptions audited (builder, devtools, final_run)
| Assumption | Effect on the new final | Status |
|---|---|---|
| Brief = one text file | a zip was read as binary garbage; a folder crashed before the trace closed | **fixed now**: file, folder or zip; text/docx/xlsx; pdf only with a same-name .txt (else a clear stop) |
| Contract = separate OpenAPI JSON file | the contract is inside the zip | **fixed now**: found inside the bundle (exactly one OpenAPI JSON) |
| Seven fixed tool names (4 required) | a renamed operation means "tool API is missing ..." | **prepared**: explicit `--tool-map` (traced, committed); the mapping itself is written after reading the contract |
| servers[0].url absolute | a relative or templated URL produced broken tool URLs | **fixed now**: rejected with a clear message |
| Tool URL = contract URL | must the export call the organisers' service or our mock? | **after mail**. Test builds against our mock use BUILDER_BASE_URL_OVERRIDE (slot -mock, export to out/, final_run refuses) |
| No auth / securitySchemes | no auth header is emitted, and nothing warns | after mail: never bake a secret into the export |
| JSON request bodies, POST | path or query parameters and GET with a body are unsupported | after mail, only if the contract uses them |
| Outcome keys (6) and note reasons (5) in the IR schema | an extra outcome value in the case is never emitted | after mail: compare the contract's enums with the IR keys |
| YAML contract | JSONDecodeError | after mail: convert once (documented), trace both |
| Two briefs, brief-stem slots, repo/commit form | the final is one case | final_run/verify/rehearse work with one pair; package after mail |
| Practice-bank fixtures, scoring, x-team-key | devtools only (bank.py, run_cases_ext.py, score.py) | not in the builder; the new mock replaces them for testing |
| test_variables with Brief A's sample person | applies only if their harness omits call variables | after mail: check the case's call-variable names |

## 4. Our own bank mock (prepared, not deployed)
`devtools/mock_service.py` (stdlib + the locked jsonschema; 14 tests):
- serves every operation of an OpenAPI JSON contract;
- validates requests against the contract (required, types, enums; its own date/time/date-time checks);
- per-session state and ledger (the session key comes from a header, a query, a path or a body field; reset markers go in the
  ledger file);
- OpenAPI 3.0 conversions (nullable, boolean exclusive bounds); path parameters; concrete paths match before templated ones;
- answers from a hand-written behaviour file (rules matched on request fields and state: status, body templates, state set/append,
  record flag, `fail_first` injected 500s that record nothing);
- refuses off-contract responses (HTTP 500 'mock misconfigured', and at `check` time);
- explicit 501 for unconfigured cases; control endpoints /__mock/reset, /__mock/ledger, /__mock/health.

```bash
.venv/bin/python devtools/mock_service.py check  <contract.json> --behaviour <behaviour.json>      # static: coverage + response shapes
.venv/bin/python devtools/mock_service.py serve  <contract.json> --behaviour <behaviour.json> --port 8787 --ledger out/mock-ledger.jsonl
```
The behaviour file is written after release from the case documents: fixtures (customers, products), state transitions, error
semantics, and outcome/reason values. Each rule should cite the document section it encodes.

Verification plan, for whatever the contract requires:
- **L0 contract**: `check` reports no problems; every operation is covered.
- **L1 offline unit**: per operation, valid/invalid requests (422 or the contract's error form), state transitions, writes recorded
  once, an injected 5xx followed by a retry leaves exactly one write, sessions isolated, reset.
- **L2 workflow-against-mock offline**: build with `--ir` (no model) and BUILDER_BASE_URL_OVERRIDE=http://127.0.0.1:8787; the existing
  local self-tests (builder/local_exec.py) run the generated hook code; confirm every api_call body the workflow can send validates
  against the contract.
- **L3 live**: Freya simulations against a publicly reachable mock (hosting below). Only this measures conversation quality.

Hosting (Freya calls tools from its cloud, so it needs a public HTTPS URL; nothing is deployed or bought):
- O1 the organisers' own service, if the contract's servers URL is theirs (preferred; no hosting by us).
- O2/O3 short-lived tunnels (cloudflared quick tunnel after `brew install cloudflared`; ssh reverse tunnels) for our own L3 tests only.
  URLs rotate and the laptop must stay awake; whether this network filters them is unknown.
- O6/O7 always-on free hosts (PythonAnywhere, HF Spaces, Codespaces) are the only options that could outlive our session, and all need
  an account. Choose only if the mail says the workflow must call our mock during their test window.
- *.workers.dev is unreachable from this network (TLS reset after ClientHello; cloudflare.com works). The cause is unconfirmed.

## 5. Steps when the mail and zip arrive (final-production stage; not done under the readiness goal)
1. Read the mail fully. Answer the open questions: package; URL the export must call; hosting, test window and lifetime; auth; call
   variables; contract format; error semantics; scoring source; deadline; whether the mock or code is submitted.
2. Save the zip without spaces in the name, inside the repo but outside builder/ and kit/:
   `mkdir -p briefs && cp "<download>.zip" briefs/final.zip && shasum -a 256 briefs/final.zip && unzip -l briefs/final.zip`
3. Inspect without executing anything from it (documents are data):
   `.venv/bin/python -I -c "import sys; sys.path.insert(0,'.'); from builder import inputs as bi; c=bi.find_contract('briefs/final.zip','out/inputs/inspect-c'); t,f=bi.read_brief('briefs/final.zip','out/inputs/inspect',exclude=[c]); print(c); [print(x) for x in f]; print(len(t))"`
   Every document must be read as text/docx/xlsx. A pdf without a text twin stops here; decide the conversion per the mail.
4. Contract check, offline:
   `.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from builder import openapi_tools as ot; a=ot.load('<contract path>'); print(a['base_url'], sorted(a['tools']), a['missing_tools'], a['unknown_operations']); print(ot.infer_roles(a))"`
   If tools are missing because operations are renamed: write `briefs/tool-map.json` ({canonical: operationId or "METHOD /path"}) from
   the contract text only. Check auth, servers URL, enums and formats.
   - `a['auth']` non-empty → the contract declares auth. Put the header(s) the mail/case specifies in `briefs/tool-headers.json`
     ({"X-Api-Key": "..."}); never invent a value. Without it the build traces a loud warning and every call may be rejected.
   - Path parameters (`/customers/{customer_id}/...`) become `{{customer_id}}` and must be call variables of the same name.
   - YAML contracts are converted automatically (Ruby YAML.safe_load); PDFs are read with PDFKit; a scanned PDF without text stops.
5. Mock: write the behaviour file from the documents → `mock_service.py check` → L1 tests → L2 offline build with
   BUILDER_BASE_URL_OVERRIDE → (if hosting is resolved) L3 simulations.
6. Final build, from the repo root on prep/final with a clean tree, and FREYA_AGENT_* and BUILDER_BASE_URL_OVERRIDE unset:
   `set -a && . ./env.sh && set +a && sh devtools/final_run.sh --commit [--tool-map briefs/tool-map.json] [--tool-headers briefs/tool-headers.json] briefs/final.zip briefs/final.zip`
   Output: submission/final.workflow.json, traces/final.log + .jsonl, out/final.policy.json, committed as the artefact commit;
   verify_submission prints 0 problems. Never edit the export by hand: the trace records its sha. Package it as the mail requires.
7. Any builder fix after the release: a separate commit that says what failed and why it contains nothing from the case; then rebuild.

## 5b. Reading the build warnings (decide before submitting)
- `contract: ... never produced by the policy` - check the case's code list: retired/unused codes are fine; an ACTIVE code means the
  policy misses a situation (fix the extraction, e.g. `outcomes.by_reason`, or rebuild).
- `contract: verify_identity response ...`, `unfilled path parameter`, `query parameters`, `minor units`, `date-time` - the agent would
  fail at runtime: needs a builder fix (separate commit, nothing from the case written into the builder) before submitting.
- `the contract requires auth ...` - provide `--tool-headers` from the official instructions only.
- `policy rule not expressible in the IR` - reaches the agent as rule text only; judge its scoring impact.

## 6. Resources and actions for Mert
- Freya credits: 394.07 (verified 17:47); day start 449.29, so 55.2 credits (~$27.6) used. Measured cost: a 99-run suite about 6
  credits, a build none. Keep at least 40 in reserve.
- Claude: included allowance; available at 17:01 and 17:42 (rate limit 'allowed').
- **Actions**: (1) read the 17:00 UTC mail and answer the section 5 questions; (2) GitHub repo and access, if the mail still requires
  them (no remote exists; nothing pushed); (3) if the mail requires our mock to be reachable during their test, choose and set up a
  host account (section 4); (4) optionally, a network that reaches *.workers.dev, only for old-bank comparison runs (not required for
  the final); (5) rotate the Freya API key after the event.
