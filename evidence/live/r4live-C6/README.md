R4-live: raw-material brief (tests/variants/brief-r4-live.md: mail, policy, superseding memo, reason-code list) built automatically with the
C6 builder (6b709f5) into slot r4live (workflow 27deb038…, agent b6838e4e…); simulation ee8fff11-8a4f-4f12-9e7d-d92b6727deb5, 6 cases x 3 plain.
Result: conversation 18/18 pass; ledger 6/18. All 6 promise runs correct with the memo's values (late deadline 2026-10-21; 26 Oct no longer a
holiday). All 12 non-promise runs: the hosted bank mock rejected close_call because it only accepts the PUBLIC brief's reason names
("this outcome needs one of these reasons: callback, no_callback, ...") - a limit of this test design (custom reason codes on the kit's mock),
not visible in the OpenAPI. The builder sent exactly the brief's codes (CB, HRD, RES, FAIL). For the final, each hidden brief ships its own
tool API, presumably with a mock accepting that brief's vocabulary.
