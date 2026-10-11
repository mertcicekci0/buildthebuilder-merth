# Platform and bank behaviour: verified findings

Each line says how it was established. "Verified" = observed in our own run on 2026-10-10; raw evidence in `results/probes/`.

## Practice bank (direct calls on practice sessions, `probes/bank_api_probe.py`, `results/probes/bank_api.json`)
- Verified: `get_account` before both checks -> `{"ok": false, "error": "identity not verified"}` (HTTP 200).
- Verified: phone answer given in words (`"four four seven one"`) is COUNTED as a wrong answer. Normalise to digits before calling.
- Verified: a DOB like `"14 March 1988"` was accepted as correct by the mock, but the ledger records the raw `args`; the brief requires YYYY-MM-DD, so always send ISO.
- Verified: once a field is verified, later calls for it return `verified: true` regardless of the answer.
- Verified: day/month swap -> `verified: false, hint: "day_month_swapped"`, `wrong_answers_left` unchanged, ledger `hinted: true`.
- Verified: 3 wrong answers -> `wrong_answers_left: 0`; further calls -> `ok: false, "identity check is over"`.
- Verified: PUB-10 fault = first `create_promise` returns HTTP 500 `{"ok": false, "error": "service unavailable, nothing was recorded"}`; retry succeeds; ledger `failures.create_promise = 1`.
- Verified: validation errors are HTTP 200 `ok:false` (`unknown product_id`, `date must be YYYY-MM-DD`, `this product already has a promise`, `unknown reason`, `unknown queue`, `this outcome needs one of these reasons: ...`). The bank enforces enums, one promise per product, call closed; it does NOT enforce policy (dates, amounts, notes on transfer...).
- Verified: request without `customer_id` -> HTTP 404 `unknown customer_id`, and nothing appears in the customer's ledger (candidate write-abort mechanism for a pre-hook; not yet verified inside a workflow).
- Verified: after `close_call`, every tool (including a second `close_call`) -> `"the call is already closed"`.
- Verified: ledger = `{verified, wrong, hinted, promises, notes, callbacks, transfers, outcome, reason, failures, calls[{n, tool, args, status, result}], now}`. The scorer can see every argument the agent sent.

## Platform
- Verified: workspace has 1 pre-installed workflow ("Freya Bank Collections Base") identical to `kit/examples/base-agent.json` plus platform-filled `agent_config` and test variables.
- Verified: platform-filled `agent_config.llmConfig` = model `experimental-5`, **temperature 0.7**, maxTokens 1000; STT/TTS language `tr`; `callTimeoutSettings.silenceTimeout` 10 s, `maximumDuration` 600 s.
- Verified (`results/probes/lab_v1_readback.json`): saving a workflow with code nodes, api_request nodes, pre/post hooks on pre_actions, a top-level lifecycle tool with pre-hook, a `date` extraction, a `code_exec` tool in a conversation node and an `is_tool_call` transition strips NOTHING; the platform only adds defaults (`assertions`, `interruptible`, `mode: "exact"`, null `max_node_turns`, ...). Dry run returned `{"valid": true, "dryRun": true}`.
- Verified: `freya.save_workflow` twice (create, then update) leaves exactly one published version with model `experimental-5`.
- Observed 2026-10-10 ~19:50 UTC: `POST /api/v2/chat/message` returned 504 on the first turn for both the lab workflow and the organisers' base workflow while a 13-call simulation was running. Not yet known whether this is load-related or persistent.

## Lab v1 (simulation b7cc9735, call timeline in `results/lab-v1-sim/calls/LAB-1_r1.txt`)
- Verified Q-B: code node reads call variable `now`; writes str/list/dict (lists/dicts render as JSON in prompts). `{{now}}` and ISO dates are substituted VERBATIM in tts (no speech/Turkish reformatting observed) -> pre-format spoken dates in code.
- Verified Q-C: re-entering an engine-run api_request node re-bakes `{{var}}` defaults (2nd create_promise carried amount 111, date 2026-10-19).
- Verified Q-D: a pre-hook that RAISES blocks the HTTP request (no callback reached the ledger; `<node>_response.status_code = 500`, `error = pre_process_code raised an exception`). A pre-hook that pops `customer_id` sends the request, which gets 404 and touches no ledger. => write guards are feasible.
- Verified Q-E: post-hook on a pre_action runs; fields it adds to `result['data']` appear in the flattened variables (`v_dob_response.data.lab_post`).
- Verified Q-F: top-level lifecycle `close_call` (end_of_call) fired after the end_call node, outcome from `{{close_outcome}}`, pre-hook removed empty reason -> ledger outcome `promise`, reason null. No model fill needed when all placeholders resolve.
- Verified Q-G: `is_tool_call` transition -> model called `transition_to_finish`, routed.
- Partly Q-H: model-called code_exec tool runs; its argument arrives as `params: {"x": ...}` in the call (not as local `x`, not in `vars` during execution); `lab_code_tool_args.x` appears in the table afterwards. Next: read `params`.
- Verified Q-I (TRAP): built-in `date` extraction anchors on the REAL clock: "I can pay tomorrow" -> 2026-10-11 although now = 2026-11-20. Never rely on built-in relative-date extraction.
- Verified Q-J: `config.maxRetries: 1` -> engine retried the HTTP 500 after ~1 s, then ok; ledger shows 2 calls, failures.create_promise = 1.
- Verified (TRAP): node-level `response_mapping` `data.products` -> variable NOT set. Lists arrive only as flattened keys `<node>_response.data.products.<i>.<field>`; reconstruct in code.
- Verified (TRAP): flattened response keys persist across calls when a later response lacks them (`write_promise_response.data.promise_id = pr1` survived the later `ok:false` call). Route on state written by our own hooks/code, not on raw flattened keys.
- Observed: in this run agent turns took 1-1.5 s; chat route returned 504 repeatedly (simulations worked). `PUT /simulations/{id} {"status":"completed"}` ends a stuck simulation (verified).

## Lab v2 (simulation f1f00c90, `results/lab-v2-sim/calls/LAB-2_r1.txt`)
- Verified Q-H2: a model-called `code_exec` tool receives its arguments as a local dict `params` (`params_seen = yes`). The model sees only `{"status": "success", "data": null, ...}`: a code tool has no result channel back to the model except variables (+ a later prompt that shows them).
- Verified Q-K: when a model-called api tool's pre-hook raises, no HTTP request is sent, and the model receives `{"status": "error", "error": "pre_process_code raised an exception", "message": "<traceback incl. our exception text>"}`; variables written by the pre-hook before raising persist (`staged_notes = [p1:refused]`). => a guard can block a write AND tell the model why.
- Inconclusive Q-L: the node re-rendered with `{{verdict}}`, but my harness prompt ("repeat the tool result") made the model echo the raw tool JSON. Not a reliable feedback channel without a cleaner test.
- Design consequence: model -> state reports via code tools (clean success to the model); write guards via raising pre-hooks with an instructive message; date decisions precomputed per product into the prompt.
