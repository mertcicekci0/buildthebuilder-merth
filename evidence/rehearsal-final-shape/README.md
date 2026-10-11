# Clean-checkout rehearsal in the final's new shape (2026-10-11 17:42-17:45 Istanbul)

Input: ONE zip (input-zip-listing.txt) holding the bank documents (kit brief A as docs/01-collections-policy.md, brief B as
docs/02-multi-product-memo.md) and the service contract (kit tool API as contract/bank-services.json). Practice material only; the real
case is unreleased. Command: `sh devtools/rehearse.sh archive/rehearsal-final-shape-20261011T144241Z 9c44283 final <zip> <zip>`
(the same zip as brief and contract; the builder finds the single OpenAPI JSON inside).

- Code commit C = 9c44283 (prep/final). Fresh clone: final_run.sh installed the locked env incl. the dev group (`uv sync --frozen`) and
  ran the offline-test gate: 127 passed (the gate is no longer skipped in a fresh checkout).
- Trace lists every bundle file and how it was read (contract excluded from the brief text). Real extraction: 2 Claude Opus 5.5 calls,
  status success. Policy checks round 1, lint, 3 local self-tests, dry run valid, save + publish (workflow d7470ec2…, agent e1ab3a06…,
  version 1), read-back, newest published version, export. 146 s.
- Artefact commit S = aafe4ee in the clone: submission/final.workflow.json, traces/final.log, traces/final.jsonl, out/final.policy.json.
  verify_submission: 0 problems. Secret scan clean.
- Extracted IR vs the hand-written A+B reference: 0 exact differences, 3 wording differences (ir_diff.txt).

Limits: practice documents in a practice zip, kit contract with the kit's tool names (no tool map needed); no simulation, no bank or
mock calls, so no conversation quality is shown. The later final_run.sh commit adding --tool-map (pass-through only) was tested with a
stubbed build, not in this rehearsal.
