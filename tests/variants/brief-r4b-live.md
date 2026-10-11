# Brief R4b: Freya Bank collections pilot (raw material with internal codes and system values; runs on the kit tool API)

Freya Bank is fictional. Every customer, debt and payment here is synthetic. This is our own generalisation test: raw bank material
(a forwarding mail, a policy extract, a memo that changes it, a code list), written so that it can be tested live on the kit's tool API.

---

## Mail from the collections operations lead, 5 October 2026

Hi team,

below is what you need for the voice-assistant pilot. I pasted the relevant part of our call policy, Gül's memo from last week (it wins
wherever it disagrees with the policy) and the reason codes the core system expects in `close_call`. Please do not use the old
codes from the 2025 pilot; the list below is the current one.

Thanks, Deniz

---

## Collections Call Policy v3 (extract), March 2026

### Call data
Every call starts with `customer_id` (pass it to every tool call), `first_name`, `last_name`, `title` (Mr or Ms) and `now`
(the current date and time, for example 2026-10-14T11:00). All date rules use `now`, never the real clock.

### Opening
The assistant's first sentence says that it is Freya Bank's virtual assistant and that the call is recorded. It then asks
"Am I speaking with {title} {last_name}?". Before identity is verified it never mentions collections, a debt, a payment, an amount or
the product, to anyone. Only "yes", "speaking", "that's me" or the customer's own name confirm the person; anything else gets one
more ask, and if it is still unclear the call ends as code C5.

### Someone else answers
Give no reason for the call and say nothing about the account, even to family members who offer to pay. Ask when the customer can
be reached. Callbacks are booked for today or the next business day, between 10:00 and 19:00 (both included), and never earlier
than `now`; book with `schedule_callback`, then end the call as code C1. If the offered time is outside these limits, ask once for
another time; if that is outside too, do not book and end as code C2. A wrong number ends as C3. If the customer has died, say
"I am very sorry for your loss.", ask nothing else and end as C4.

### Identity
Two questions, one at a time: the date of birth first, then the last four digits of the phone number on file. Call `verify_identity`
after each answer; send the date of birth as YYYY-MM-DD; a date given only in numbers is day, then month, then year. A wrong answer
may be asked again; three wrong answers in total end the check: do not state any debt, tell the customer to use the mobile app or a
branch, and end as V1. If the customer will not answer, explain once that the questions protect their account; a second refusal
(or asking a second time what the call is about instead of answering) ends the call as V2.

### The debt
After both answers pass, call `get_account`. Say you are calling from the collections team about a late payment, name each product in
words ("credit card ending" plus the card's last four digits, "personal loan", "car loan"), and state the past-due amount and the
days late.

### Business days
Monday to Friday, except bank holidays: 2026-10-26, 2026-11-11, 2026-12-25, 2027-01-01.

### Lateness bands
| Days late | Band | Latest promise date | Persuasion attempts |
|---|---|---|---|
| 1 to 9 | early | 12th business day after today | 1 |
| 10 to 45 | late | 7th business day after today | 2 |

A promise date must be today or later, a business day and not after the latest promise date. A weekend day or holiday that is not
after it: offer the next business day. A date after it: say the latest date you can record and offer it; refusing that is a flat
refusal. "Next Monday" means Monday of the following calendar week; "this Monday" the current week. A vague period ("next week",
"after payday"): ask once for the exact day; if none comes, propose the latest promise date.

### Amounts and persuasion
The floor is `minimum_payment` for a credit card and `instalment_amount` for a loan. Always ask for the full past-due amount first,
never ask what the customer can pay; you propose every amount. If the full amount is too much, offer the floor once and say the rest
stays overdue. Any amount from the floor up to the full amount is accepted; below the floor say it cannot be recorded and offer the
floor once. First question: "Can you make this payment today?". A date or an amount the customer proposes is never a refusal.
A flat refusal or a non-answer uses one attempt:
- Attempt 1: the full amount (or the floor if the customer already said the full amount is too much) on the last business day of this
  week; if today is that day, the last business day of next week.
- Attempt 2, late band only: the floor on the latest promise date.
When the attempts are used up, stop and end as N1. Never offer a discount, waiver or plan.

### Special situations
- Illness, a death in the family or job loss: one short sentence of sympathy, no persuasion; offer the customer support team. Accepted:
  `transfer_to_agent` with queue `support`, end as T3. Declined: end as N2. This comes before persuasion and restructuring.
- "I already paid": say payments can take one business day to appear; no promise; end with outcome `paid`.
- "Not my debt" or a product closed in the past: do not argue, no promise, end as N3.
- A request for a new payment plan or lower instalments: describe no plan; `transfer_to_agent` with queue `restructuring`, end as T1.
- A request for a person: ask once whether you can help; if they insist, `transfer_to_agent` with queue `general`, end as T2.
- "Are you a robot?": say you are Freya Bank's virtual assistant and continue. "Is this a scam?": say they can verify the call by
  phoning the number on the back of their card, and continue.

### Confirming and recording
Read the product, amount and date back and ask "Do you confirm?"; call `create_promise` only after a clear yes. Say the promise is
recorded only after `create_promise` succeeds; if it fails, try once more; if it fails again, say so and end as N4. After a recorded
promise say: "You will get a text message confirming this promise." One promise per product; `add_note` is not used in this pilot.
Thank the customer and say goodbye before the call ends; `close_call` is always the last tool call.

---

## Memo from Gül Aydın, Head of Collections, 29 September 2026 — "Changes for the pilot"

Effective immediately, these replace the policy where they differ:
1. Callbacks: same day or next business day only, between 09:00 and 17:00, both included.
2. The late band's latest promise date is now the 5th business day after today.
3. 26 October is no longer a bank holiday this year; 29 October 2026 is a bank holiday instead.

Everything else in the policy stays.

---

## Outcome codes (core system extract)

Agents talk about the codes in the first column; the core system stores the values in the last two columns. `close_call` must be sent
the SYSTEM values (`outcome` and `reason` columns), never the code. Promise and paid take no reason.

| Code | Situation | outcome | reason |
|---|---|---|---|
| P1 | at least one promise recorded | `promise` | — |
| P2 | already paid | `paid` | — |
| T1 | restructuring transfer | `transferred` | `restructuring` |
| T2 | insisted on a person | `transferred` | `human` |
| T3 | hardship, support team accepted | `transferred` | `support` |
| N1 | attempts used up / refused | `no_promise` | `refused` |
| N2 | hardship, support declined | `no_promise` | `hardship` |
| N3 | dispute | `no_promise` | `dispute` |
| N4 | recording failed twice | `no_promise` | `service_error` |
| V1 | three wrong security answers | `not_verified` | `failed` |
| V2 | security questions declined | `not_verified` | `declined` |
| C1 | callback booked | `not_reached` | `callback` |
| C2 | no acceptable callback time | `not_reached` | `no_callback` |
| C3 | wrong number | `not_reached` | `wrong_number` |
| C4 | customer deceased | `not_reached` | `deceased` |
| C5 | could not confirm who answered | `not_reached` | `unclear` |

Retired codes from the 2025 pilot (do not use): X1, X2, X9. If a transfer follows a recorded promise, the transfer's code is used.
