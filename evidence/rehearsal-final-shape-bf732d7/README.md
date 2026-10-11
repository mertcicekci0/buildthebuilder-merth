# End-to-end rehearsal of the selected preparation candidate (prep/final bf732d7), 2026-10-11 17:58-18:01 Istanbul

Same input and procedure as evidence/rehearsal-final-shape (one practice zip: kit briefs A and B as documents + the kit tool API as the
contract inside the zip), now on the commit that includes all review fixes (inputs, tool map, shell, mock) and the safe rehearse.sh.
- Fresh clone at bf732d7; final_run.sh installed the locked env incl. dev group and ran the test gate: 134 passed.
- Trace: bundle files listed (contract excluded from the brief text); 2 real Claude Opus 5.5 extraction calls (success); policy checks
  round 1, lint, 3 local self-tests, dry run valid, save + publish (workflow 5c45bbfd…, agent b7e74fcc…, version 1), read-back, newest
  published version, export. 147 s.
- Artefact commit S = 96cca4a (clone): export, trace .log/.jsonl, out/final.policy.json. verify_submission 0 problems. Secret scan clean.
- IR vs reference: 0 exact / 3 text differences.
Limits: practice documents and the kit contract (no tool map needed, no auth); no simulation and no bank/mock traffic, so conversation
quality on any contract is not shown. --tool-map was verified by unit tests and dash stub tests, not in this rehearsal.
