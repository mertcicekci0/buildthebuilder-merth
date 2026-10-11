Live automatic builds on C13 (a51f13a, dev/c10) of the two independent adversarial briefs, 13:41-13:45 Istanbul, Claude Agent SDK (Team login):
- R5 (raw material: out-of-order memos, partial reversals, product-scoped/not-yet-effective memos, retired codes, renamed API incl. response fields):
  166 s, 2 model calls, policy checks/lint/3 local self-tests pass, saved f096c3a5…/agent 45f374c6…, read-back pass, published newest, exported.
  Extraction vs independent reference: 0 exact differences.
- R6 (clean spec, car finance, renamed API): 238 s, same checks pass, saved 437ed223…/agent d742f4b9…, exported. 2 exact differences
  (identity.max_wrong_total, callback.after_now) verified harmless in code (no reader of max_wrong_total; identical callback verdicts).
On the C5 builder both briefs crashed in compile (KeyError on role inference) - evidence/extraction-5.
These agents point at non-existent example servers and are not simulated.
