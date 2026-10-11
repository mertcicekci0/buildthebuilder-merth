# cf_mock: the bank mock on Cloudflare Workers

Generates a self-contained Worker from the case's contract (OpenAPI JSON or YAML) and a behaviour file (same format and semantics as
`devtools/mock_service.py`: rules with `when`, `status`, `body` templates `{{body.x}}`/`{{state.x}}`/`{{path.x}}`/`{{query.x}}`/`{{session}}`,
`set`, `append`, `fail_first`, `record`; `fixtures` per session with `"*"` as the default; `session_key`).
State is a SQLite-backed Durable Object per session (Workers free plan), so it is strongly consistent: every request for one session runs
one at a time against that session's storage. No KV.

## 1. Generate (after writing `behaviour.json` from the case documents)

```sh
.venv/bin/python devtools/mock_service.py check case/contract.json --behaviour behaviour.json   # optional static check
.venv/bin/python devtools/cf_mock/build_worker.py case/contract.yaml behaviour.json out/cf-mock --name bank-mock
#   --base-path /v1   also serve every operation under /v1 (operations are always served at the contract path from the root)
#   --strict          fail if the behaviour check reports problems (they are printed either way)
```

Writes `out/cf-mock/worker.js`, `wrangler.toml`, `package.json`. YAML is converted with Ruby `YAML.safe_load` (as `builder/inputs.py` does).

Optional local run (no login needed; uses workerd + a local SQLite DO):

```sh
cd out/cf-mock && npx --yes wrangler@3.114.0 dev --port 8799    # then curl http://127.0.0.1:8799/__mock/health
```

## 2. Deploy

```sh
cd out/cf-mock
npx --yes wrangler@3.114.0 login                         # human, once: opens the browser
npx --yes wrangler@3.114.0 deploy --dry-run --outdir /tmp/cfm-dist   # optional: proves it bundles, no login needed
npx --yes wrangler@3.114.0 deploy                        # prints https://bank-mock.<account-subdomain>.workers.dev
```

The first deploy of an account may ask to pick a workers.dev subdomain. Redeploying after a behaviour change: regenerate into the same
outdir and `deploy` again (the `v1` migration is already applied; stored session state is kept - reset it, see below).

Tool URLs: `https://bank-mock.<sub>.workers.dev` + the contract path (e.g. `/verify_identity`), or `.../v1/verify_identity` when
generated with `--base-path /v1`.

## 3. Control endpoints (always at the root)

```sh
M=https://bank-mock.<sub>.workers.dev
curl -s $M/__mock/health                                   # operations served, base path, session key
curl -s "$M/__mock/ledger?session=C1"                      # one session's ledger (every call: op, body, status, response,
                                                           #   rejected / recorded, at)
curl -s $M/__mock/ledger                                   # {session: ledger} for every session seen since the last full reset
curl -s -XPOST $M/__mock/reset -d '{"session":"C1"}'       # one session back to its fixture, its ledger and fail_first counters cleared
curl -s -XPOST $M/__mock/reset -d '{}'                     # every session
```

`recorded: true` marks the writes a scorer would read (rule has `record` and the answer was < 400); `rejected: true` marks a 422.

## 4. Keeping it running

Nothing to keep alive: a deployed Worker and its Durable Object storage stay up until deleted (`npx wrangler@3.114.0 delete`). There is
no process, tunnel or laptop involved. Free plan limits (100k requests/day, Durable Object free tier) are far above a demo's traffic.
`npx --yes wrangler@3.114.0 tail` streams live request logs if needed.

## Semantics (same as mock_service.py)

- Routing: method + path; concrete paths win over templated ones (`/accounts/search` before `/accounts/{id}`); path params are
  URL-decoded. Unknown route: 404 (not recorded).
- Request validation against the contract's request schema ($refs inlined, OpenAPI 3.0 `nullable` / boolean exclusive bounds converted):
  type, enum, const, required, properties, additionalProperties, items, lengths, pattern, numeric bounds, allOf/anyOf/oneOf/not, and
  the formats date, time, date-time. Missing required body or invalid body: 422 `{"error": "<field>: <message>"}`, recorded as rejected.
- First matching rule wins; no matching rule: 501. `fail_first: N`: the first N matching calls per session answer 500
  `injected failure` and change nothing. A rendered body that does not fit the contract's response schema: 500 `mock misconfigured`.
- The session is chosen by `session_key` (`{"body": "customer_id"}`, `{"header": ...}`, `{"query": ...}`, `{"path": ...}`; else
  `default`); each session is its own Durable Object.

## Tests

`.venv/bin/python -m pytest -q tests/test_cf_mock.py` runs the same scenarios on mock_service.Mock, the JS core (`core.js`, in-memory
store) and the generated `worker.js` (fetch handler + MockState over a fake DO storage) via `harness.mjs` under node, and requires
identical answers and ledgers.

Limitations: a header named in `session_key` must be sent by the caller (otherwise everything lands in `default`); the full-ledger
and reset-all endpoints walk sessions one by one (fine for tens to hundreds of sessions); a template inside a longer string prints
booleans as `True`/`False` like the Python reference.
