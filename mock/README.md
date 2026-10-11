# merth-bank-mock: Freya Bank statement / debt services mock

A Cloudflare Worker (ES module, no npm dependencies) that serves the seven voice services of the bank's
integration document, `sources/documents/2026-10-01-service-contract.md`, called "the contract" below.
For every test customer it returns exactly the body and HTTP status from `test-environment/test-customers.json`.

## Files

| File | What it is |
|---|---|
| `worker.js` | Routing, the lookup rules, the error envelopes, and the `CustomerState` Durable Object (ledger) |
| `data.js` | GENERATED fixture: every pack answer stored as its raw JSON text, so it goes out byte-for-byte (`0.0` stays `0.0`) |
| `wrangler.toml` | `merth-bank-mock`, `workers_dev = true`, one SQLite Durable Object binding `STATE` -> `CustomerState` (no account_id) |
| `scripts/build_data.py` | Rebuilds `data.js`: `python3 -I scripts/build_data.py "<case>/test-environment/test-customers.json" data.js` |
| `scripts/verify.py` | Checks a running mock against the whole pack: `python3 -I scripts/verify.py "<case>/test-environment/test-customers.json" http://localhost:8799` |

## Endpoints

All services are `POST`, JSON in and out. The request envelope is `{"data": {...}, "call": {...}}` (contract §3).
Only `data` decides the answer. The token, headers, `call` and `aiFlow` are not checked (contract §2 and §6).
Successful answers are `{data, result}` with `returnCode "1"`. Error answers carry `result` only, with no `data` (contract §4).

| Method + path | Request `data` | Lookup (test-customers.md) | Answers |
|---|---|---|---|
| `POST /voice/v1/cards.list` (§5.1) | `customerNo` (string or number), `aiFlow` | by `customerNo` | the pack entry (200; 404 `FB_IVR_CHANNEL_102` for 93821776; 500 `FBE00099` for 97073972). Unknown customer: 404 `FB_IVR_CHANNEL_102` |
| `POST /voice/v1/cards.balance` (§5.2) | `cardNumber` | by masked card number exactly as cards.list gave it | the pack entry (200; card `540000******7211` (94907600) waits 12 s and then answers 200). Unknown card: 404 `FB_IVR_CHANNEL_120` |
| `POST /voice/v1/statement.week.get` (§5.3) | `cardNumber` | by card | the pack entry (500 for `540000******1111` (94835408)). Unknown card: 404 `FB_IVR_CHANNEL_121` |
| `POST /voice/v1/statement.week.decide` (§5.4) | `cardNumber`, `newStatementWeek` (1-4, number or string), `statementDay` | by card, then week | the pack entry (500 for card `540000******1112` (94339861), weeks 1/3/4). The card's current week, or an unknown card: 400 `INVALID_ARGUMENTS_ERROR` |
| `POST /voice/v1/statement.week.change` (§5.5) | same as decide | by card, then week | the pack entry. Current week or unknown card: 400 `INVALID_ARGUMENTS_ERROR`. **Logged in the ledger** |
| `POST /voice/v1/contact.email.get` (§5.6) | `customerNo`, `intAddressInfoType` (E/P/B) | by `customerNo`, type ignored | the pack entry (500 for 92335586; phone-only customers 94907600, 96125950, 97631186, 97783309 get empty `ibanEmail`). Empty type: 400. Unknown customer: 400 |
| `POST /voice/v1/statement.pdf.send` (§5.7) | `customerNo`, `cardNumber`, `currencyType`, `statementDate`, `emailAddress` | by `customerNo` only | the pack entry (200 `exitState "DONE"`; 404 `FB_IVR_CHANNEL_107` for 93635512). No entry (93821776, 94907600, 96125950, 97631186, 97783309, or unknown): 404 `FB_IVR_CHANNEL_107`. **Logged in the ledger** |
| `POST /oauth2/token` (§2) | form body, not checked | none | `{"access_token":"mock-…","token_type":"Bearer","expires_in":1800}` |
| `GET /__mock/health` (also `/`, `/health`) | none | none | `{status:"ok", customers:34, cards:65, ledger:true, routes:[…]}` |
| `GET /__mock/ledger?customer=<customerNo>` | none | none | `{customer, entries:[{seq, ts, service, request:{data,call}, http, response}]}` |
| `POST /__mock/reset[?customer=<customerNo>]` | none | none | clears one customer's ledger, or all 34 customers plus `unknown` |

Other errors:
- Any service with a required field missing or empty: 400 `INVALID_ARGUMENTS_ERROR` (`"card number cannot be empty"`, `"intAddressInfoType cannot be empty"`, and so on).
  These fields are checked before the lookup.
- A body that is not JSON: 400 `INVALID_ARGUMENTS_ERROR`.
- `newStatementWeek` outside 1-4: 400.
- `currencyType` not TL/TRY/USD/EUR/GBP: 400.
- GET on a service path: 405. Unknown path: 404 with a `result` envelope (`code "NOT_FOUND"`).

Errors the mock builds itself use the contract §4 envelope: `returnCode == code`, `messageText == message`,
`type` ERROR/WARNING, a fresh 32-hex `guid` repeated in `details.guid`, and `errorOrigin` `"ivr-gateway-test:ivr-channel-v0"`.
Errors that come from the pack are returned exactly as the pack has them.

## State

Answers are **stateless**: every call returns the pack's answer, including after a `statement.week.change`.
The reasons:
- The pack says the dates are fixed and that the mock must return exactly the listed answers.
- `decide` and `change` have entries for every week except the card's original one. If we stored the new week, the pack's own lookup would break.

Write calls (`statement.week.change`, `statement.pdf.send`) are appended to a per-customer ledger, whatever their outcome, including 400/404/500.
The ledger is a SQLite-backed Durable Object (`CustomerState`, one instance per customer number via `idFromName`).
A week change is filed under the customer who owns the card. A call that can't be matched to a customer goes under `unknown`.
If the ledger write fails, the API answer still goes out.

## Ambiguities and how they were resolved

1. **Unknown customer or card** (contract §6, test-customers.md): we answer that service's 404 from §4.
   `decide`, `change` and `contact.email.get` have no 404 in §4, so they answer 400 `INVALID_ARGUMENTS_ERROR`. We never make data up.
2. **The card's current week in decide/change**: the pack has no entry for it, and §5.4 says "you'll get an error". The mock answers 400 `INVALID_ARGUMENTS_ERROR`.
3. **Customer 97073972**: `cards.list` answers 500, but the pack still has card-level answers for `540000******1201/1202/1203`.
   Lookups go by card number, as the pack says, so those cards are served.
4. **Message text for synthesized 404s**: §4 gives exact text for 102 and 107, which we use. For 120 we use the §5.2 example text. For 121 the contract has none, so we wrote our own (`"Ekstre hafta servisi hatası: kart bulunamadı"`). §5.2 says only the code matters.
5. **`statementDay` in decide/change** is not validated. The test environment's lookup ignores it, and §5.5 says the backend recalculates it.
6. **Leniency.** None of the following changes any answer the pack defines:
   - `customerNo` and `newStatementWeek` are accepted as number or string.
   - A body without a `data` wrapper is read as `data`.
   - A path without `/voice/v1/` is accepted.
   - `{{template}}` values from the Freya panel's "Test API Request" button (cloudflare-mock/README.md) map to 90000001 / `540000******1234` / week 2.
   - If the exact card key is unknown, a 16-character number whose first 6 + last 4 digits match exactly one known card is resolved to that card. Production `cards.list` sends full numbers (9 Oct recap), and that is where this helps.
7. **The 10 Oct follow-up transcript** is about the agent's wording only: currency names, asking about another card, the no-statement card `540000******2020`, and the cut day reading `totalDebt`.
   It changes no service, field or test answer. The pack already returns empty `statementDate`/`lastPayDate` for card 2020 (97631186), and the mock serves that as-is.

## Run

```bash
export npm_config_cache=<writable npm cache dir>
npx --yes wrangler@3.114.0 dev --port 8799 --local      # local, no login needed
python3 -I scripts/verify.py "<case>/test-environment/test-customers.json" http://localhost:8799
npx --yes wrangler@3.114.0 deploy                        # integration owner, after `wrangler login`
```
