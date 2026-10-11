"""Read a brief + tool API into a policy IR with the model; verify it with code. The only model-using module.

Passes (each one model call through llm.ask_json, traced):
  1. extract  - full IR from the brief and the API digest, with verbatim evidence and a precedence log.
  2. audit    - independent re-read of the brief against the IR: omissions, wrong values, wrong precedence.
               Returns the corrected IR and a change list (source quotes for every change).
  3. repair   - only if deterministic checks (policy_schema.check) still fail; at most `max_repairs` times.
               Check warnings (e.g. a holiday written in a form the check cannot read) are traced, never repaired.
Nothing from any specific brief is written here: the guide below describes the IR's meaning, not a bank's rules.
"""
import json

from . import openapi_tools as ot
from . import policy_schema as ps

GUIDE = """You fill a policy IR for a compiler that builds an outbound phone agent for a bank (or similar lender).
The compiler owns the call STRUCTURE: opening and "who is on the line" -> third party/callback or identity check ->
account lookup -> stating the debt -> negotiation per product (persuasion ladder, dates, amounts) -> one read-back and
confirmation -> recording promises -> notes for products without a promise -> transfers -> closing outcome.
You supply the POLICY: every number, name, sentence, calendar and rule, taken only from the brief and the tool API.

Field meanings:
- language: a language name, e.g. "English". accepted_confirmations / not_confirmations: literal phrases, with <...> only for call variables.
- A sentence the brief only describes (not quotes): write the shortest natural sentence that says exactly that and adds nothing.
- call_variables: the names of the call variables the brief lists for: customer id, first name, last name, title, current date-time ("now"); "" for a role the brief has no variable for. other_call_variables: the names of any further call variables the brief lists (e.g. a company name), [] if none.
- opening.first_sentence: the agent's exact first sentence satisfying every opening requirement (who it is, which bank, recording disclosure...). Do not mention the purpose if the brief forbids it before verification. If the brief quotes the sentence, copy it exactly. Otherwise avoid words the brief reserves for confirming an action later in the call (e.g. "recorded", "confirmed", "saved"): for a recording disclosure say "we record this call" rather than "this call is recorded".
- opening.identity_question: the question confirming the person, with <variable> placeholders for call variables, e.g. "<title> <last_name>" if the brief says so.
- opening.forbidden_before_verification: words/topics that must not be said before identity is verified.
- opening.max_asks: how many times in total the agent asks the identity question before ending as "could not confirm" (e.g. "anything else gets one more ask" = 2).
- identity.refusals_before_close: the number of refusals to answer the security questions that ends the call (e.g. "a second refusal ends the call" = 2).
- identity.questions: in the brief's order. api_value MUST be one of verify_identity's allowed field values. format: "date" (sent as YYYY-MM-DD), "digitsN" (N digits, e.g. digits4), "upper_nospace", or "text". numeric_order: DMY or MDY for dates said as numbers (from the brief), else "".
- identity.swapped_hint_value: the API hint value meaning day/month swapped, or null if none.
- product_types: one entry per product type in the API enum. spoken: how to name it, with <field> placeholders for get_account product fields (e.g. a card's last digits). floor_field: the get_account field that is the minimum acceptable amount for that type, or null.
- debt_intro / product_sentence: how the debt is stated after verification. product_sentence placeholders: <spoken> <past_due> <days_late> <floor>.
- after_verification_sentence: a sentence the brief requires right after identity is verified and before the debt is stated (e.g. a disclosure or notice), exactly as quoted; "" if none. Do not repeat debt_intro here.
- calendar: business_weekdays as numbers 0=Monday..6=Sunday; holidays as YYYY-MM-DD (resolve the year from the brief); week_start 0=Monday. next_weekday: what "next <weekday>" means: "following_week" (that weekday in the following calendar week; the default when the brief does not say) or "next_occurrence" (the first such weekday after today, e.g. "next Friday" said on a Wednesday is in two days).
- bands: lateness bands from days late: min/max days (inclusive), deadline_business_days = the N in "the N-th business day after today" (deadline_unit "business_days"); if the brief counts plain days ("within 10 days of today", "10 calendar days"), put that N and deadline_unit "calendar_days". attempts = persuasion attempts allowed.
- first_ask_single / first_ask_multi: the first question about paying (one product / several products).
- ladder: the persuasion steps in order. amount: full | floor | full_or_floor ("full, or the floor if the customer already said the full amount is too much"). date: today | end_of_week (last business day of this week, or of next week if today is that day) | deadline. bands: band names the step applies to ([] = all).
- callback: allowed days ("today", "next_business_day", or "within_business_days:N"), window start/end HH:MM (inclusive), after_now (true if the time may not be earlier than "now"), alternative_asks (how many times the agent asks for another time after an unacceptable one, e.g. "ask once for another time" = 1).
- multi_product / notes: whether several products and notes apply; notes.default_reason is the semantic reason (refused, hardship, dispute, paid or service_error) for a product left without promise or explicit outcome; outcomes.note_reasons maps it to the API value.
- transfer_queues: map restructuring / human / support to the API's queue values ("" if the brief has no such transfer).
- outcomes: map each semantic outcome to the close_call outcome value of THIS API, and each semantic reason (reasons, note_reasons) to the exact API/brief value. If the brief names outcomes with codes (a code list), map them to the API's values. reasons.<key> is the reason sent with close_call in that situation: "" when the brief lists no reason for it (e.g. an outcome whose table row has no reason) or has no such situation. If the brief closes a situation under a different outcome than its name suggests (e.g. "already paid" closed as no_promise with reason X), put that outcome value in outcomes.<key> and X in reasons.<key>.
- recording: retries after a failed write (e.g. "try once more" = 1), the exact sentence required after a recorded promise, the read-back question.
- fixed_sentences: exact sentences the brief requires (deceased condolence, robot/AI answer, scam answer, already-paid explanation, farewell). Use the brief's wording when it gives one.
- stage_rules: the brief's rules rewritten as short imperative instructions for the phone agent, grouped by stage. Include EVERY behavioural rule, including ones the fields above already encode (the agent needs the wording). Keep the brief's numbers, qualifiers ("both included", "in total", "once", "second", "before and after", "later than") and quoted phrases. Never add rules the brief does not state. How to write them:
  * The phone agent never sees the brief: no rule or section numbers, no "the brief", "Brief X" or "rule 30"; replace every cross-reference with the behaviour it points to. Leave out statements about tools that are not used.
  * Describe actions by effect, not by tool or parameter names: "transfer to the restructuring queue", "end the call as <outcome> / <reason>", "record the promise", "mark this product as dispute". The compiler gives each stage its own tools.
  * Say what each ending ends: the whole call, or only the current product. When several products are possible, product-level endings read "mark this product as <reason> and continue with the next product".
  * Apply precedence everywhere: when a later rule (memo, amendment, multi-product section) replaces an earlier one only in some situations, rewrite every stage rule that still states the earlier behaviour as an explicit "With one product: ... With several products: ..." (or "Before ...: ... After ...: ..."). No superseded wording may remain unconditional in any stage.
  * Stages that are shown to the agent together must not contradict each other: "always" is in every stage; negotiation, multi_product, confirmation and recording form one prompt.
- evidence: for each important value (bands, holidays, deadlines, floors, callback window, outcomes, identity questions, required sentences), the path and a SHORT VERBATIM quote copied character for character from the brief.
- precedence: when a later memo, amendment or notice changes an earlier rule, record both quotes and why the later one governs. Use the governing rule everywhere.
- unmapped_rules: brief rules you could not express anywhere above (should be rare; they are still put in stage_rules).
Return only the IR."""


def api_digest(api, roles):
    item = ot.product_item_schema(api) or {}
    lines = [ot.summary(api, roles), "get_account product fields: " + json.dumps(item.get("properties", {}))]
    if api.get("api_description"):
        lines.append("API description: " + api["api_description"])
    for t, c in api["tools"].items():
        if c.get("summary"):
            lines.append(f"{t}: {c['summary']}")
        for f in c["fields"]:
            if f.get("description"):
                lines.append(f"  {t}.{f['name']}: {f['description']}")
    return "\n".join(lines)


def extract(llm, brief_text, api, roles, trace, max_repairs=2):
    schema = ps.schema()
    digest = api_digest(api, roles)
    base = f"TOOL API (authoritative for field names and allowed values):\n{digest}\n\nBRIEF (authoritative for policy):\n<<<\n{brief_text}\n>>>"
    try:
        ir = llm.ask_json("extract the policy IR from the brief and the tool API", GUIDE, base + "\n\nFill the policy IR.", schema)
    except Exception as e:  # noqa: BLE001  one bounded retry (a timeout or a malformed answer); no loop
        trace.warn(f"extraction call failed ({type(e).__name__}: {str(e)[:200]}); retrying once")
        ir = llm.ask_json("extract the policy IR (retry after a failed call)", GUIDE, base + "\n\nFill the policy IR.", schema)
    trace.step("extraction pass returned", evidence=len(ir.get("evidence", [])), precedence=len(ir.get("precedence", [])))
    audit_schema = {"type": "object", "additionalProperties": False, "required": ["changes", "ir"],
                    "properties": {"changes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                          "required": ["path", "problem", "quote"],
                                                                          "properties": {"path": {"type": "string"}, "problem": {"type": "string"}, "quote": {"type": "string"}}}},
                                   "ir": schema}}
    try:
        audit = _audit(llm, base, ir, audit_schema)
    except Exception as e:  # noqa: BLE001  the first-pass IR is complete on its own; the checks below still run
        trace.warn(f"audit call failed ({type(e).__name__}: {str(e)[:200]}); continuing with the first-pass IR")
        audit = {"changes": [], "ir": ir}
    changes = audit.get("changes", [])
    trace.step(f"audit pass: {len(changes)} change(s)", changes=changes)
    ir = audit.get("ir") or ir
    for attempt in range(max_repairs + 1):
        check_warnings = []
        problems = ps.check(ir, api, roles, brief_text, warnings=check_warnings)
        trace.test(f"policy checks (round {attempt + 1})", "pass" if not problems else f"{len(problems)} problem(s)", problems=problems,
                   warnings=check_warnings)
        for w in check_warnings:
            trace.warn("policy check: " + w)
        if not problems or attempt == max_repairs:
            return ir, problems, changes
        try:
            ir = llm.ask_json("repair the IR so the deterministic checks pass", GUIDE,
                              base + "\n\nIR:\n" + json.dumps(ir, indent=1) + "\n\nThese checks failed; fix exactly these, from the brief and the API, change nothing else:\n- " +
                              "\n- ".join(problems), schema)
        except Exception as e:  # noqa: BLE001
            trace.warn(f"repair call failed ({type(e).__name__}: {str(e)[:200]}); keeping the current IR")
            return ir, problems, changes
    return ir, problems, changes


def _audit(llm, base, ir, audit_schema):
    return llm.ask_json("audit the IR against the brief for omissions, wrong values and precedence", GUIDE + """

AUDIT TASK: another reader produced the IR below. Re-read the whole brief sentence by sentence. For every rule, number,
date, name, sentence or exception: is it in the IR, correct, and does the latest governing version win (memos,
amendments, code lists)? In particular:
(a) every numbered or bulleted rule of the brief is carried by a field or a stage rule; a rule with no carrier is an omission;
(b) qualifiers survive rule by rule: bounds, "in total", "whichever", "once", "second", "before and after", "later than", priorities;
(c) every precedence entry is applied in stage_rules too: no superseded wording left unconditional (e.g. a one-product
    ending that ends the whole call, next to a several-products rule that says to continue with the next product);
(d) no two rules shown to the agent together give different actions for the same situation;
(e) stage rules follow the writing rules above (no rule numbers, no tool names, endings say call or product).
List every problem with a verbatim quote, then return the full corrected IR. Change nothing that is correct.
Empty changes list if the IR is right.""",
                        base + "\n\nIR TO AUDIT:\n" + json.dumps(ir, indent=1), audit_schema)
