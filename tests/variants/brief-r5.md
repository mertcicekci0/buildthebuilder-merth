# Brief R5: Tidewell Mutual Bank, arrears calls pilot (raw material pack)

Tidewell Mutual Bank is fictional. Every customer, debt and payment here is synthetic. This is our own generalisation test (R5), written to look like raw bank material: a forwarding mail, a policy extract, three memos and a code list. Nothing has been merged into one specification.

---

**From:** Priya Oduya, Head of Collections Operations
**To:** Voice assistant build team
**Date:** Wednesday 4 November 2026
**Subject:** FW: policy pack for the assistant pilot

Hi all,

Here is everything the assistant needs for the arrears pilot. Pilot calls run from Monday 9 November 2026 to Friday 26 February 2027, and the assistant makes no calls outside those dates. A pilot customer can be behind on up to three accounts.

The attachments are in the order they came off the shared drive, which is not the order they were issued. Please read the pack like this:

1. Where two documents disagree, the one with the later issue date wins. The attachment order means nothing.
2. A memo that is limited to one product wins for that product over any general document, whatever the dates.
3. A rule only counts from its effective date. If a rule's effective date is after the date of the call, the call follows the rules as they stood before it.
4. A footnote is part of the rule it hangs on. Where a later document restores an earlier rule, it restores its footnotes too.
5. CP-7 still quotes the old three-digit disposition numbers. Annex D is the current code list: use it to turn every disposition into what the dialler takes, and use the replacement wherever a code has been retired.

Section 8 of CP-7 is for branch colleagues and is not in the extract. Some branches now open on Saturday mornings; that does not make Saturday a business day for collections.

Thanks,
Priya

Attachments: (A) HC-26-19, (B) CP-7 extract, (C) MF-26-04, (D) Annex D, (E) HC-26-14, (F) PG-26-31

---

## Attachment A: Memo HC-26-19, "Corrections to HC-26-14"

**From:** Marcus Feld, Head of Collections. **Issued:** 20 October 2026. **Effective:** immediately.

Team, after three weeks on HC-26-14 we are correcting part of it. Only the points named below change. Points 3, 4 and 7 of HC-26-14 stand as written.

- **Point 1 (callbacks).** The 08:30 to 20:00 hours are withdrawn: callback times go back to CP-7 2.3. The shorter booking horizon (today or the next business day) stays.
- **Point 2 (security).** The membership number question stays, but it is now asked last, after the postcode: customers need time to find their membership card. The two-wrong-answers limit is withdrawn. The limit is three wrong answers in total, as in CP-7 2.4.
- **Point 5 (confirmation sentence).** Letters are too slow. After a recorded promise say: "You'll receive a text message and an email confirming this arrangement."
- **Point 6 (third attempt in S3)** is withdrawn. S3 keeps the attempts of CP-7 3.4.
- **Holidays.** Monday 4 January 2027 is a Scottish holiday only. Every pilot customer lives in England or Wales, so take it off the list: it is a business day for the pilot.
- **Reminder.** The floor on every loan, UPL and MTR alike, is its `instalment_due` (CP-7 3.3). Some colleagues have been accepting less than that.

---

## Attachment B: Policy extract, CP-7 "Outbound arrears calls by the automated assistant"

**Version 3. Issued:** 1 June 2026. **Owner:** Collections Policy. Sections 2 and 3 only.

### 2.1 Call data
With every call the dialler passes `customer_id` (pass it to every tool call), `forename`, `surname`, `honorific` (Mr, Mrs, Ms, Mx or Dr) and `dial_time`, the local date and time of the call, for example 2026-11-18T10:30. Every date and time rule uses `dial_time` as today and now, never the real clock.

### 2.2 Opening
The first sentence, word for word: "Hello, this is the automated assistant of Tidewell Mutual Bank. This call is recorded." Then ask: "Could I speak with {honorific} {surname}, please?"

Before security is passed, do not mention arrears, an overdue amount, a payment, collections, the account or the product, to anyone, family members included.

Only "yes", "speaking", "that's me" or the customer saying their own name confirms. "Hello", "who's calling?" and "what is it about?" do not: ask once more. If it is still unclear after the second ask, close as 505. If the person asks a question first, answer it; the ask after that is the second ask.

Asked whether this is a robot: "I'm Tidewell Mutual Bank's automated assistant.", then continue. Asked whether this is a scam: "You can check this call by ringing the number on the back of your card or on your statement.", then continue.

### 2.3 Someone else answers
Give no reason for the call. Ask when the customer can be reached. A callback can be booked for today or one of the next two business days, from 09:30 to 18:00, both included,¹ and later than `dial_time`. Book it with `schedule_callback` and close as 601. If the time offered is outside this, ask once for another; if that is outside too, book nothing and close as 502. Not the customer's number: close as 503. The customer has died: say "I'm very sorry to hear that.", ask nothing more, close as 504. A third party may never answer security questions or hear anything about the account, even if they offer to pay.

¹ On 24 December and 31 December the contact centre closes at 14:00. A callback on either of those days must be at 14:00 or earlier.

### 2.4 Security
Ask one question at a time, in this order: the date of birth, then the postcode of the home address. Call `verify_identity` after each answer. Send the date of birth as YYYY-MM-DD; a date said only in numbers is day, then month, then year. Send the postcode in capitals with no spaces. Never use the mother's maiden name question; it was withdrawn from phone security in 2025.

If `verify_identity` returns the hint `dmy_swapped`, ask the customer to say the month as a word. That retry is not a wrong answer. A wrong answer may be asked again. Three wrong answers in total end the check, whichever question they were on. After that, do not discuss the account: say "I'm sorry, I can't go any further on this call. Please ring the number on your statement or visit a branch." and close as 401.

If the customer will not answer, explain once: "These questions are there to protect your account." If they refuse again, close as 402. Asking what the call is about instead of answering counts as refusing.

### 2.5 Business days
Monday to Friday, except bank holidays: 1 January, 3 April, 6 April, 4 May, 25 May, 31 August, 25 December and 28 December 2026; 1 January, 4 January (Scotland), 26 March and 29 March 2027.

### 3.1 Stating the arrears
When security is passed, call `get_account`. Say: "I'm calling from Tidewell's arrears team about overdue payments." Then one sentence per account: "Your <product> is <days> days overdue, with <amount> outstanding." Product names: CRD "credit card ending <card_ending>", UPL "personal loan", ODR "overdraft on your current account ending <acct_ending>", MTR "car finance agreement ending <agreement_ending>". Say every amount in pounds and pence exactly as the system gives it (for example £1,284.37); never round.

### 3.2 Stages

| Days past due | Stage | Latest promise date | Persuasion attempts |
|---|---|---|---|
| 1 to 10 | S1 | 10th business day after today | 1 |
| 11 to 35 | S2 | 7th business day after today | 2 |
| 36 to 89 | S3 | 5th business day after today | 2 |

A promise date must be today or later, a business day, and not after the stage's latest promise date. A weekend day or holiday that is not after the latest date: offer the next business day. A date after it: say the latest date that can be recorded and offer it; refusing that is a flat refusal. "Next Wednesday" means the Wednesday of the following calendar week, "this Wednesday" the one in the current week; weeks start on Monday. A vague period ("after payday", "end of the month"): ask once for the exact day; if none comes, propose the latest promise date.

### 3.3 Amounts
Always ask for the full overdue amount first. Never ask what the customer can afford: the assistant proposes every amount. The floor is `min_pay` for CRD² and ODR, and `instalment_due` for UPL and MTR. If the customer says the full amount is too much, offer the floor once and say the rest stays overdue. Any amount from the floor up to the full overdue amount, both included, is accepted. Below the floor: say it cannot be recorded and offer the floor once; if it is not accepted, that is a flat refusal.

² Where a card's overdue amount is £30.00 or less there is no part payment: the floor is the full overdue amount.

### 3.4 Getting the promise
First ask: "Are you able to pay the overdue amount today?" With several accounts: "Are you able to pay all of these today?" A date or an amount the customer proposes is never a refusal. A flat refusal or a non-answer uses one persuasion attempt; a no to today with no date and no amount is a flat refusal.

- Attempt 1, S1 and S2: the full overdue amount (or the floor, if the customer already said the full amount is too much) on the last business day of the current week. If today is that day, the last business day of next week.
- Attempt 1, S3: the floor on the last business day of the current week, with the same rule if today is that day.
- Attempt 2, S2 and S3 only: the floor on the latest promise date.

When the attempts are used up, stop, do not repeat an offer the customer refused, and close as 301.

### 3.5 Several accounts
State every overdue account, one sentence each, before asking for anything. If the customer cannot pay all of them today, go through the accounts one at a time in the order `get_account` lists them. Do not ask about today again. Each account starts at attempt 1 and attempts are counted per account. If the customer names one date for everything and it is after one account's latest promise date, record that account on its own latest date and say so in the read-back; the others keep the named date. Read all promises back together, ask for one confirmation, then call `create_promise` once per account.

For each account left without a promise, call `add_note` with that account and a note code: NPR (refused), FHD (hardship), DSP (dispute), APD (already paid) or SER (system failure). A dispute or an "already paid" claim on one account does not end the call. On any transfer, file no notes at all, before or after it.

### 3.6 Hardship
Illness, a bereavement or the loss of a job: one short sentence of sympathy and no persuasion. Offer the Welfare Team. Accepted: transfer to desk `welfare_team` and close as 209. Declined: close as 302 (note FHD). If the customer still names a date or an amount, record the promise. Hardship comes before persuasion and before restructuring.

### 3.7 Other situations
- "I've already paid": say "Payments can take up to two business days to show on your account.", take no promise, close as 102 (several accounts: note APD). A claim that the account was paid off or closed in the past is a dispute.
- "This isn't my debt": do not argue, take no promise, close as 303.
- A payment plan or lower instalments: describe nothing, transfer to desk `borrower_solutions`, close as 201. Promises already recorded stay; promises agreed but not yet recorded are not recorded.
- A request for a person: ask once whether you can help. If they insist, transfer to desk `contact_centre` and close as 202.
- The customer says twice they do not want to talk: close as 301 (before security is passed: 402).
- A question the tools cannot answer: say you do not have that information.
- Never offer a discount, a waiver, a payment holiday, a plan or any term this policy does not list.

### 3.8 Recording
Read back the account, the amount and the date and ask "Shall I set that up for you?". Record only after a clear yes. If the customer changes something at that point, check it again and read it back again. One promise per account; a recorded promise cannot be changed on this call (the customer can use online banking). Say a promise is recorded only after `create_promise` succeeds. If it fails, try once more; if it fails again, say it could not be recorded and close as 304 (several accounts: keep the recorded ones and note the failed account SER). After a recorded promise say: "You'll receive a text message confirming this arrangement."

### 3.9 Closing
Thank the customer and say goodbye, then make exactly one `close_call`, always the last tool call. At least one promise recorded: 101, unless a transfer followed it, in which case that transfer's disposition. No promise, several accounts: if every account is noted APD, close as 102; otherwise use the disposition that goes with the note of the first account, in `get_account` order, whose note is not APD (NPR 301, FHD 302, DSP 303, SER 304).

---

## Attachment C: Memo MF-26-04, "Motor finance: no part payments"

**From:** Motor Finance Operations. **Issued:** 2 October 2026. **Effective:** immediately. **Applies to:** MTR agreements only.

Under our hire purchase terms a part payment does not stop a default notice. From today, on an MTR agreement:

- There is no part payment. The floor is the full overdue amount.
- If the customer says the full amount is too much, there is no lower amount to offer: treat it as a flat refusal (it uses an attempt).
- Everything else in CP-7 applies to MTR as to any other account.

---

## Attachment D: Annex D, "Call result codes", CRS version 4

**Issued:** 1 October 2026. Replaces CRS version 3.

`close_call` takes the mnemonic as `result_code` and the sub-code as `reason_code`. Leave `reason_code` out where the table shows "–". The three-digit disposition is for MI reports only and is never sent. The dialler still accepts retired codes for old cases; the assistant must never send one.

| Disposition | Mnemonic | Sub-code | Meaning | Status |
|---|---|---|---|---|
| 101 | PTP | – | at least one promise recorded | active |
| 102 | PIF | – | customer says already paid | active |
| 201 | XFR | RST | payment plan request, transferred | active |
| 202 | XFR | HUM | insisted on a person, transferred | active |
| 203 | XFR | FSU | hardship, accepted Financial Support Unit | active from 1 Oct 2026 |
| 209 | XFR | WEL | hardship, accepted Welfare Team | retired 30 Sep 2026, use 203 |
| 301 | NPTP | REF | refused, attempts used up | active |
| 302 | NPTP | HRD | hardship, declined support | active |
| 303 | NPTP | DSP | disputed | active |
| 304 | NPTP | SYS | system failure | retired 30 Sep 2026, use 305 |
| 305 | NPTP | TEC | promise could not be recorded | active |
| 401 | NOID | IDF | three wrong security answers | active |
| 402 | NOID | IDR | refused security | active |
| 501 | NOCON | CB | callback booked | active |
| 502 | NOCON | NCB | no callback time agreed | active |
| 503 | NOCON | WN | wrong number | active |
| 504 | NOCON | DEC | customer deceased | active |
| 505 | NOCON | UNC | could not confirm who answered | active |
| 601 | CBK | – | callback booked | retired 30 Sep 2026, use 501 |

Note codes (`add_note`, field `note_code`): NPR no promise, refused; FHD financial hardship; DSP dispute; APD already paid; TEC system failure (active from 1 Oct 2026); SER system failure (retired 30 Sep 2026, use TEC); OTH other (not for the assistant).

Product codes (`product_code`): CRD credit card; UPL unsecured personal loan; ODR arranged overdraft; MTR car finance (hire purchase).

---

## Attachment E: Memo HC-26-14, "Q4 changes to CP-7"

**From:** Marcus Feld, Head of Collections. **Issued:** 15 September 2026. **Effective:** 1 October 2026.

Team, the following changes to CP-7 apply from 1 October 2026. Everything else in CP-7 stays.

1. Callbacks: today or the next business day only, between 08:30 and 20:00, both included.
2. Security: add a third question, the last three digits of the customer's membership number, asked first, before the date of birth. Send it as three digits. Two wrong answers in total now end the check.
3. Stages: S1 is now 1 to 14 days past due and S2 is 15 to 35. S1's latest promise date is now the 14th calendar day after today; if that day is not a business day, it is the last business day before it.
4. The Welfare Team becomes the Financial Support Unit on 1 October: the desk is `financial_support`. Annex D has the close code.
5. After a recorded promise say: "You'll receive a letter confirming this arrangement."
6. S3 gets a third persuasion attempt: the floor amount, today.
7. Friday 27 November 2026 (Founders' Day) is a bank holiday.
8. Branch colleagues: the new arrears posters are in the stationery cupboard.

---

## Attachment F: Memo PG-26-31, "Spring 2027 changes"

**From:** Product Governance. **Issued:** 28 October 2026. **Effective:** Monday 1 March 2027.

From the effective date:

- ODR: no part payments. The floor on an overdraft becomes the full overdue amount.
- Callbacks may also be booked on Saturdays, from 09:00 to 13:00.
- After a recorded promise say: "Your arrangement is now showing in the Tidewell app."
