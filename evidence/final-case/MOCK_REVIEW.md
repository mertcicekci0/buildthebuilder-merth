# Mock review (independent, 2026-10-11 21:28 Istanbul) and deployment

Deployed: https://merth-bank-mock.mertcicekci29.workers.dev (Cloudflare account ac8e0b29…, version 424aa0d2-cefb-43bb-9031-6bb23217a6ae,
21:23 Istanbul). Source: mock/ at commit acfe5b2 (unchanged since).

- Mock author's check: mock/scripts/verify.py against `wrangler dev --local`: 1154 checks, 0 failures.
- Independent reviewer: replayed every entry of test-environment/test-customers.json through worker.fetch under Node: 617 requests,
  all 7 services, 34 customers, every card/week key: 0 status/body mismatches. Unknown customers/cards get the contract §6 errors
  (cards.list 404 102, cards.balance 404 120, week.get 404 121, decide/change/contact 400, pdf.send 404 107). Auth: test environment
  does not check tokens/headers/call (§2, §6); the mock never returns 401.
- Findings kept by decision: template-variable fallback (`{{customerNumber}}` -> sample record) is what cloudflare-mock/README.md
  line 50 recommends ("Doing the same in your own services makes testing easier"); leniency (missing statementDay, missing `data`
  wrapper, full card numbers matched by first 6 + last 4, TRY accepted) only tolerates malformed calls and never changes a pack answer;
  JS prototype-name inputs (e.g. customerNo "constructor") would produce a Cloudflare 500 page - not a realistic caller input.
- Connectivity: *.workers.dev is unreachable from this Mac on both networks used (TLS reset after ClientHello); reachability is proven
  only through Freya tool calls (see the live-test evidence).
