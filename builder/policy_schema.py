"""Policy IR: the typed contract between the brief reader (model) and the compiler (code).

`schema()` is the JSON Schema used as the extraction's structured output (no min/max keywords, closed objects).
`check(ir, api, roles, brief_text)` runs the deterministic checks; every problem is a sentence a repair prompt can use.
"""
import datetime
import re

from . import openapi_tools as ot


def _o(props, required=None):
    return {"type": "object", "properties": props, "required": required if required is not None else list(props), "additionalProperties": False}


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
SL = {"type": "array", "items": S}
IL = {"type": "array", "items": I}
NS = {"type": ["string", "null"]}

STAGES = ["always", "opening", "third_party", "identity", "negotiation", "multi_product", "confirmation", "recording", "closing"]
REASON_KEYS = ["restructuring", "human", "support", "refused", "hardship", "dispute", "service_error", "failed", "declined",
               "callback", "no_callback", "wrong_number", "deceased", "unclear", "paid"]
NOTE_KEYS = ["refused", "hardship", "dispute", "paid", "service_error"]


def schema():
    return _o({
        "bank_name": S, "language": S, "currency_word": S,
        "call_variables": _o({k: S for k in ("customer_id", "first_name", "last_name", "title", "now")}),
        "other_call_variables": SL,
        "opening": _o({"first_sentence": S, "identity_question": S, "accepted_confirmations": SL, "not_confirmations": SL,
                       "max_asks": I, "forbidden_before_verification": SL}),
        "identity": _o({"questions": {"type": "array", "items": _o({"api_value": S, "ask": S, "format": S, "numeric_order": S})},
                        "max_wrong_total": I, "swapped_hint_value": NS, "swapped_instruction": S, "failed_message": S,
                        "refusal_explanation": S, "refusals_before_close": I}),
        "product_types": {"type": "array", "items": _o({"api_type": S, "spoken": S, "floor_field": NS})},
        "debt_intro": S, "product_sentence": S,
        "calendar": _o({"business_weekdays": IL, "holidays": SL, "week_start": I}),
        "bands": {"type": "array", "items": _o({"name": S, "min": I, "max": I, "deadline_business_days": I, "attempts": I})},
        "first_ask_single": S, "first_ask_multi": S,
        "ladder": {"type": "array", "items": _o({"attempt": I, "amount": {"type": "string", "enum": ["full", "floor", "full_or_floor"]},
                                                 "date": {"type": "string", "enum": ["today", "end_of_week", "deadline"]}, "bands": SL})},
        "callback": _o({"days": SL, "start": S, "end": S, "after_now": B, "alternative_asks": I}),
        "multi_product": _o({"enabled": B, "per_product_order": S, "one_confirmation": B, "clip_shared_date_to_deadline": B}),
        "notes": _o({"enabled": B, "skip_on_any_transfer": B, "default_reason": S}),
        "transfer_queues": _o({"restructuring": S, "human": S, "support": S}),
        "outcomes": _o({"promise": S, "paid": S, "transferred": S, "no_promise": S, "not_verified": S, "not_reached": S,
                        "reasons": _o({k: S for k in REASON_KEYS}), "note_reasons": _o({k: S for k in NOTE_KEYS})}),
        "recording": _o({"retries_after_failure": I, "after_promise_sentence": S, "read_back_question": S}),
        "fixed_sentences": _o({k: S for k in ("deceased", "robot", "scam", "paid", "farewell")}),
        "stage_rules": _o({k: SL for k in STAGES}),
        "evidence": {"type": "array", "items": _o({"path": S, "quote": S})},
        "precedence": {"type": "array", "items": _o({"topic": S, "superseded_quote": S, "governing_quote": S, "why": S})},
        "unmapped_rules": SL,
    })


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[*_`>|#]", " ", str(s))).strip().lower()


def check(ir, api, roles, brief_text=""):
    p = []
    close_enum = ot.enum_of(api, "close_call", roles.get("close_outcome"))
    o = ir["outcomes"]
    for k in ("promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"):
        if close_enum and o[k] not in close_enum:
            p.append(f"outcomes.{k} = {o[k]!r} is not an allowed close_call outcome {close_enum}")
    note_enum = ot.enum_of(api, "add_note", roles.get("note_reason"))
    if ir["notes"]["enabled"]:
        for k, v in o["note_reasons"].items():
            if note_enum and v and v not in note_enum:
                p.append(f"outcomes.note_reasons.{k} = {v!r} is not an allowed add_note reason {note_enum}")
    queue_enum = ot.enum_of(api, "transfer_to_agent", roles.get("transfer_queue"))
    for k, v in ir["transfer_queues"].items():
        if queue_enum and v and v not in queue_enum:
            p.append(f"transfer_queues.{k} = {v!r} is not an allowed queue {queue_enum}")
    verify_enum = ot.enum_of(api, "verify_identity", roles.get("verify_field"))
    if not ir["identity"]["questions"]:
        p.append("identity.questions is empty")
    for i, q in enumerate(ir["identity"]["questions"]):
        if verify_enum and q["api_value"] not in verify_enum:
            p.append(f"identity.questions[{i}].api_value {q['api_value']!r} not in verify_identity values {verify_enum}")
        if q["format"] not in ("date", "text", "upper_nospace") and not re.fullmatch(r"digits\d*", q["format"]):
            p.append(f"identity.questions[{i}].format {q['format']!r} must be date, digitsN, text or upper_nospace")
    item = ot.product_item_schema(api) or {}
    pfields = set(item.get("properties", {}))
    type_enum = (item.get("properties", {}).get(roles.get("product_fields", {}).get("product_type", ""), {}) or {}).get("enum", [])
    for i, t in enumerate(ir["product_types"]):
        if t.get("floor_field") and pfields and t["floor_field"] not in pfields:
            p.append(f"product_types[{i}].floor_field {t['floor_field']!r} is not a get_account product field {sorted(pfields)}")
        if type_enum and t["api_type"] not in type_enum and t["api_type"] != "*":
            p.append(f"product_types[{i}].api_type {t['api_type']!r} not in API product types {type_enum}")
        for ph in re.findall(r"<(\w+)>", t["spoken"]):
            if pfields and ph not in pfields:
                p.append(f"product_types[{i}].spoken uses <{ph}>, not a product field")
    for t in type_enum:
        if t not in {x["api_type"] for x in ir["product_types"]}:
            p.append(f"product type {t!r} from the API has no entry in product_types")
    for h in ir["calendar"]["holidays"]:
        try:
            datetime.date.fromisoformat(h)
        except ValueError:
            p.append(f"calendar.holidays has a non-ISO date {h!r}")
    if not ir["calendar"]["business_weekdays"] or any(d not in range(7) for d in ir["calendar"]["business_weekdays"]):
        p.append("calendar.business_weekdays must be numbers 0 (Monday) to 6 (Sunday)")
    bands = sorted(ir["bands"], key=lambda b: b["min"])
    if not bands:
        p.append("bands is empty")
    for a, b in zip(bands, bands[1:]):
        if b["min"] != a["max"] + 1:
            p.append(f"bands {a['name']} ({a['min']}-{a['max']}) and {b['name']} ({b['min']}-{b['max']}) leave a gap or overlap")
    for b in bands:
        if b["deadline_business_days"] < 0 or b["attempts"] < 0:
            p.append(f"band {b['name']} has a negative number")
    names = {b["name"] for b in bands}
    for s in ir["ladder"]:
        for bn in s["bands"]:
            if bn not in names:
                p.append(f"ladder attempt {s['attempt']} names unknown band {bn!r}")
    for k in ("start", "end"):
        if not re.fullmatch(r"\d{2}:\d{2}", ir["callback"][k]):
            p.append(f"callback.{k} must be HH:MM")
    for d in ir["callback"]["days"]:
        if d not in ("today", "next_business_day") and not re.fullmatch(r"within_business_days:\d+", d):
            p.append(f"callback.days value {d!r} must be today, next_business_day or within_business_days:N")
    if brief_text:
        nb = _norm(brief_text)
        for i, e in enumerate(ir.get("evidence", [])):
            q = _norm(e["quote"]).strip(" .\"'")
            if q and q not in nb:
                p.append(f"evidence[{i}] ({e['path']}) quote is not in the brief verbatim: {e['quote'][:80]!r}")
        for h in ir["calendar"]["holidays"]:
            if not _date_mentioned(h, brief_text):
                p.append(f"holiday {h} is not mentioned in the brief")
    return p


def _date_mentioned(iso_date, text):
    d = datetime.date.fromisoformat(iso_date)
    t = text.lower()
    month = d.strftime("%B").lower()
    pats = [iso_date, f"{d.day} {month}", f"{month} {d.day}", f"{d.day:02d}/{d.month:02d}", f"{d.day}/{d.month}", f"{d.day}.{d.month}.", d.strftime("%d.%m.")]
    return any(x in t for x in pats) or bool(re.search(rf"\b{d.day}(st|nd|rd|th)?\s+(of\s+)?{month}", t)) or bool(re.search(rf"{month}\s+{d.day}(st|nd|rd|th)?\b", t))
