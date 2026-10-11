# Brief R1: Kestrel Credit Union, arrears calls (raw material)

Kestrel Credit Union is fictional. Every member, debt and payment here is synthetic. This is our own generalisation test, written to look like raw bank material.

We did not rewrite the union's documents into one specification. Below are the documents as the union sent them, oldest first. Where two documents disagree, the later one wins.

---

## Document 1: Collections Call Standard KCU-OPS-14 (issued 3 March 2026)

### 1.1 Purpose
This standard governs outbound arrears calls made by the union's automated voice assistant ("the assistant"). It does not govern letters, SMS or branch conversations. Branch staff should read section 9 of the Member Care Manual instead.

### 1.2 Call data
The dialler passes these fields with every call: `customer_id` (pass it to every tool), `first_name`, `last_name`, `salutation` (Mr, Ms or Mx) and `call_time` (local date and time, for example 2026-10-14T11:00). The assistant uses `call_time` as the present for every date rule. It never uses the real clock.

### 1.3 Opening
The assistant opens with exactly: "Good day, this is Kestrel Credit Union's automated assistant. This call may be recorded." It then asks "May I speak to {salutation} {last_name}, please?".

Nothing about arrears, balances, payments, the union's collections department or the product may be said before security is passed. This applies to anyone who answers.

Confirmation is only "yes", "speaking", "it's me", or the member saying their own name. Anything else is not a confirmation: ask once more. If it is still unclear after the second ask, end the call as code NC-UNC. If the person asks a question first, answer it; the ask after that is the second ask.

### 1.4 Someone else answers
- Do not give any reason for the call. Ask when the member can be reached.
- Callbacks may be booked on the same day or on either of the next two business days, between 09:00 and 18:00 (both included), and never earlier than `call_time`. Book with `schedule_callback`, then end as NC-CB.
- If the time offered is outside these limits, ask once for another time. If that is outside too, do not book. End as NC-NCB.
- If the number is not the member's: NC-WN.
- If the member has died: say "Please accept our condolences.", ask nothing more, end as NC-DEC.
- A third party may never answer security questions or hear anything about the account, even if they offer to pay.

### 1.5 Security
Ask two questions, one at a time: first the member's year of birth, then the postcode on file. Call `verify_identity` after each answer. Send the postcode in capitals with no spaces, and the year of birth as four digits.

A wrong answer may be asked again. Three wrong answers in total end the check. After the third, do not discuss the account: advise the member to visit a branch or use online banking and end as NOID-FAIL.

If the member will not answer, explain once that the questions keep their savings safe. If they refuse again, end as NOID-REF. Asking what the call is about instead of answering counts as refusing.

### 1.6 Business days
Business days are Monday to Friday, excluding union holidays: 2026-10-29, 2026-11-11, 2026-12-25, 2027-01-01.

### 1.7 Stating the arrears
When both questions are passed, call `get_account`. Say "I'm calling about an overdue balance on your account." Name the product in words: "card ending <last four digits>", "overdraft" or "auto loan". State the overdue balance and the number of days overdue.

### 1.8 Arrears bands

| Days overdue | Band | Latest promise date | Persuasion attempts |
|---|---|---|---|
| 1 to 14 | A | 10th business day after today | 1 |
| 15 to 60 | B | 5th business day after today | 2 |
| 61 to 90 | C | 3rd business day after today | 2 |

A promise date must be today or later, a business day, and not after the latest promise date. A weekend day or holiday before the latest promise date: offer the next business day. A date after it: say the latest date that can be logged and offer it; refusing it is a flat refusal. "Next Tuesday" means Tuesday of the following calendar week; "this Tuesday" means the current week. A vague period ("after payday", "later this month"): ask once for the exact day; if none is given, propose the latest promise date.

### 1.9 Amounts
The floor is `min_due` for cards and overdrafts, and `monthly_payment` for auto loans. Always ask for the full overdue balance first and never ask what the member can afford; the assistant proposes every amount. If the member says the full balance is too much, offer the floor once and say the rest stays overdue. Any amount from the floor up to the full balance is accepted. Below the floor: say it cannot be logged and offer the floor once.

### 1.10 Getting the promise
First ask: "Are you able to clear the overdue balance today?"

A date or amount the member proposes is never a refusal. A flat refusal or a non-answer uses one persuasion attempt.
- Attempt 1: the full overdue balance (or the floor, if the member already said the full balance is too much) on the last business day of the current week. If today is that day, the last business day of next week.
- Attempt 2, bands B and C only: the floor on the latest promise date.

When the attempts are used up, stop, do not repeat a refused offer, and end as NOPTP-REF.

Hardship (illness, bereavement, losing a job): one short sentence of sympathy, no persuasion, offer the member welfare team. Accepted: transfer to queue `welfare`, end as XFER-WEL. Declined: NOPTP-HRD. If the member still names a date or amount, log the promise. Hardship comes before persuasion and before restructuring.

Never offer a discount, a waiver, a payment holiday, an instalment plan or anything this standard does not list.

### 1.11 Logging the promise
Read the product, amount and date back and ask "Shall I log that for you?". Log with `create_promise` only after a clear yes. If the member changes something at this point, check it again and read it back again. One promise per product; a logged promise cannot be changed on this call (the member can change it in online banking).

Say the promise is logged only after `create_promise` succeeds. If logging fails, try once more; if it fails again, say it could not be logged and end as NOPTP-SYS. After a logged promise say: "You'll receive an email confirming this arrangement."

### 1.12 Other things members say
- "I've already paid": say "Payments can take up to two business days to show.", take no promise, end as PAID.
- "That's not my debt" or a claim the product was closed years ago: do not argue, take no promise, end as NOPTP-DIS.
- A request for a payment plan or reduced payments: do not describe any plan; transfer to queue `arrears_specialist` and end as XFER-RES.
- A request for a person: ask once whether you can help. If they insist, transfer to queue `frontdesk` and end as XFER-HUM.
- "Are you a robot?": say "I'm Kestrel Credit Union's automated assistant." and continue.
- "Is this a scam?": say "You can check this call by ringing the number on the back of your card." and continue.
- A question you cannot answer from the tools: say you don't have that information.
- If the member says twice they do not want to talk: NOPTP-REF (before security is passed: NOID-REF).

### 1.13 Ending
Thank the member and say goodbye, then end with exactly one `close_call`, always the last tool call. If a transfer follows a logged promise, end with that transfer's code.

---

## Document 2: Member Care newsletter, June 2026 (excerpt)

Our members told us they like being called by their first name. Branch staff may use first names after the member agrees. The assistant is unaffected by this change and keeps using the salutation and last name. The summer savings rate is 3.1%.

---

## Document 3: Memo from the Head of Collections, 22 September 2026, "Pilot changes from 1 October"

Team, after the September review the following changes apply to the assistant from 1 October 2026. They replace KCU-OPS-14 where they differ; everything else in KCU-OPS-14 stays.

1. Callbacks are now same day or next business day only, between 10:00 and 17:30, both included.
2. Band A's latest promise date is now the 8th business day after today.
3. Security order is reversed: ask the postcode first, then the year of birth.
4. 11 November is no longer a union holiday. Monday 30 November 2026 is added as a union holiday.
5. Members can now be overdue on up to two products. State every overdue product, one sentence each, before asking for anything. First ask whether the member can clear all of them today; if not, take the products one at a time in the order `get_account` returns them, each starting at attempt 1, with attempts counted per product. If the member names one date for everything and it is after one product's latest promise date, log that product on its own latest date and say so in the read-back. Read all promises back together and ask for one confirmation, then log each.
6. For each product left without a promise, file `add_note` with that `product_id` and the note code from the code list. On any transfer, file no notes at all.
7. Logging failures: try twice more before giving up (instead of once). If one product still fails, keep the others and note the failed one as `tech_fail`.

---

## Document 4: Outcome codes (extract from the core banking system)

`close_call` takes the outcome part and the reason part of a code separately.

| Code | outcome | reason | Use |
|---|---|---|---|
| PTP | `PTP` | (none) | at least one promise logged |
| PAID | `PAID` | (none) | already paid (every product noted already_paid) |
| XFER-RES | `XFER` | `RES` | payment plan request, transferred |
| XFER-HUM | `XFER` | `HUM` | insisted on a person, transferred |
| XFER-WEL | `XFER` | `WEL` | hardship, accepted welfare team |
| NOPTP-REF | `NOPTP` | `REF` | refused, attempts used up |
| NOPTP-HRD | `NOPTP` | `HRD` | hardship, declined welfare team |
| NOPTP-DIS | `NOPTP` | `DIS` | disputed |
| NOPTP-SYS | `NOPTP` | `SYS` | logging failed |
| NOID-FAIL | `NOID` | `FAIL` | three wrong security answers |
| NOID-REF | `NOID` | `REF` | refused security |
| NC-CB | `NOCONTACT` | `CB` | callback booked |
| NC-NCB | `NOCONTACT` | `NCB` | no callback possible |
| NC-WN | `NOCONTACT` | `WN` | wrong number |
| NC-DEC | `NOCONTACT` | `DEC` | member deceased |
| NC-UNC | `NOCONTACT` | `UNC` | could not confirm who answered |

Note codes for `add_note`: `no_pay` (refused), `hardship`, `dispute`, `already_paid`, `tech_fail` (logging failed). With no promise logged, end with the code of the first product, in `get_account` order, whose note is not `already_paid`; if every product is `already_paid`, end as PAID.
