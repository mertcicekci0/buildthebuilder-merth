"""Policy IR: the typed contract between the brief reader (model) and the compiler (code).

`schema()` is the JSON Schema used as the extraction's structured output (no min/max keywords, closed objects).
`check(ir, api, roles, brief_text, warnings=None)` runs the deterministic checks; every problem is a sentence a repair prompt can use.
Findings that may be wrong about a correct IR (a holiday whose date the brief writes in a form we do not read) are not
problems: they go to the optional `warnings` list, are traced, and never trigger a model repair.
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
# sentences the system itself speaks verbatim (English defaults in compile_workflow.PHRASE_DEFAULTS)
PHRASE_KEYS = ["verified", "promise_recorded", "promises_recorded", "promise_item", "record_failed_for", "and", "record_failed",
               "connecting", "goodbye"]


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
        "debt_intro": S, "product_sentence": S, "after_verification_sentence": S,
        "calendar": _o({"business_weekdays": IL, "holidays": SL, "week_start": I,
                         "next_weekday": {"type": "string", "enum": ["following_week", "next_occurrence"]}}),
        "bands": {"type": "array", "items": _o({"name": S, "min": I, "max": I, "deadline_business_days": I,
                                                "deadline_unit": {"type": "string", "enum": ["business_days", "calendar_days"]}, "attempts": I})},
        "first_ask_single": S, "first_ask_multi": S,
        "ladder": {"type": "array", "items": _o({"attempt": I, "amount": {"type": "string", "enum": ["full", "floor", "full_or_floor"]},
                                                 "date": {"type": "string", "enum": ["today", "end_of_week", "deadline"]}, "bands": SL})},
        "callback": _o({"days": SL, "start": S, "end": S, "after_now": B, "alternative_asks": I}),
        "multi_product": _o({"enabled": B, "per_product_order": S, "one_confirmation": B, "clip_shared_date_to_deadline": B}),
        "notes": _o({"enabled": B, "skip_on_any_transfer": B, "default_reason": {"type": "string", "enum": NOTE_KEYS}}),
        "transfer_queues": _o({"restructuring": S, "human": S, "support": S}),
        "outcomes": _o({"promise": S, "paid": S, "transferred": S, "no_promise": S, "not_verified": S, "not_reached": S,
                        "reasons": _o({k: S for k in REASON_KEYS}), "note_reasons": _o({k: S for k in NOTE_KEYS}),
                        "by_reason": _o({k: S for k in REASON_KEYS})}),
        "recording": _o({"retries_after_failure": I, "after_promise_sentence": S, "read_back_question": S}),
        "fixed_sentences": _o({k: S for k in ("deceased", "robot", "scam", "paid", "farewell")}),
        "phrases": _o({k: S for k in PHRASE_KEYS}),
        "locale": _o({"month_names": SL, "weekday_names": SL, "date_pattern": S, "decimal_separator": S, "thousands_separator": S,
                      "today_words": SL, "tomorrow_words": SL, "day_after_tomorrow_words": SL, "next_words": SL, "this_words": SL, "vague_words": SL}),
        "stage_rules": _o({k: SL for k in STAGES}),
        "evidence": {"type": "array", "items": _o({"path": S, "quote": S})},
        "precedence": {"type": "array", "items": _o({"topic": S, "superseded_quote": S, "governing_quote": S, "why": S})},
        "unmapped_rules": SL,
    })


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[*_`>|#]", " ", str(s))).strip().lower()


def check(ir, api, roles, brief_text="", warnings=None):
    p = []
    close_enum = ot.enum_of(api, "close_call", roles.get("close_outcome"))
    o = ir["outcomes"]
    for k in ("promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"):
        if close_enum and o[k] not in close_enum:
            p.append(f"outcomes.{k} = {o[k]!r} is not an allowed close_call outcome {close_enum}")
    for k, v in (o.get("by_reason") or {}).items():
        if close_enum and v and v not in close_enum:
            p.append(f"outcomes.by_reason.{k} = {v!r} is not an allowed close_call outcome {close_enum}")
    reason_enum = ot.enum_of(api, "close_call", roles.get("close_reason"))
    for k, v in (o.get("reasons") or {}).items():
        if reason_enum and v and v not in reason_enum:
            p.append(f"outcomes.reasons.{k} = {v!r} is not an allowed close_call reason {reason_enum}")
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
    for b in bands:
        if b["max"] < b["min"]:
            p.append(f"band {b['name']} has max {b['max']} below min {b['min']}")
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
    loc = ir.get("locale") or {}
    for k, n in (("month_names", 12), ("weekday_names", 7)):
        if loc.get(k) and len(loc[k]) != n:
            p.append(f"locale.{k} must have {n} names (or be empty), not {len(loc[k])}")
    if brief_text:
        nb = _norm(brief_text)
        for i, e in enumerate(ir.get("evidence", [])):
            q = _norm(e["quote"]).strip(" .\"'")
            if q and q not in nb:
                p.append(f"evidence[{i}] ({e['path']}) quote is not in the brief verbatim: {e['quote'][:80]!r}")
        for h in ir["calendar"]["holidays"]:
            try:
                mentioned = _date_mentioned(h, brief_text, loc.get("month_names"))
            except ValueError:
                continue   # a non-ISO holiday is already a problem above
            if not mentioned and warnings is not None:
                warnings.append(f"holiday {h} was not found written in the brief (kept; check it)")
    return p


_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_ORD = r"(?:st|nd|rd|th)?"
_RANGE = r"(?:\s*(?:-|\u2013|\u2014|to|through|thru|until|till|and|&)\s*(\d{1,2})" + _ORD + r")?"


def _month_re(month, extra_months=None):
    names = {_MONTHS[month - 1], _MONTHS[month - 1][:3]} | ({"sept"} if month == 9 else set())
    if extra_months and len(extra_months) == 12 and str(extra_months[month - 1]).strip():
        names.add(re.escape(str(extra_months[month - 1]).strip().lower()))   # the brief's language (IR locale)
    return r"\b(?:" + "|".join(sorted(names, key=len, reverse=True)) + r")\b\.?"


def _date_mentioned(iso_date, text, extra_months=None):
    """Is this date written in the text? Reads ISO, day-month and month-day with full or short month names and ordinals
    ("25 Dec", "December 25th", "the 25th of December", "Friday 25 December"), day ranges ("24-26 December",
    "24 to 26 December", "December 24-26") and numeric dates ("25.12.2026", "25-12-2026", "12/25/2026", "25/12", "25.12.")."""
    d = datetime.date.fromisoformat(iso_date)
    t = re.sub(r"\s+", " ", re.sub(r"[*_`]", "", str(text).lower().replace("\u00a0", " ")))
    if iso_date in t:
        return True
    mon = _month_re(d.month, extra_months)

    def within(lo, hi):
        lo = int(lo)
        hi = int(hi) if hi else lo
        return lo <= d.day <= hi
    for m in re.finditer(r"\b(\d{1,2})" + _ORD + _RANGE + r"\s+(?:of\s+)?" + mon, t):
        if within(m.group(1), m.group(2)):
            return True
    for m in re.finditer(mon + r"\s+(?:the\s+)?(\d{1,2})" + _ORD + _RANGE + r"(?!\d)", t):
        if within(m.group(1), m.group(2)):
            return True
    for m in re.finditer(r"(?<![\d.,/-])(\d{1,4})([./-])(\d{1,2})(?:\2(\d{2,4}))?(?![\d/-]|\.\d)", t):
        a, sep, b, c = m.group(1), m.group(2), m.group(3), m.group(4)
        if len(a) == 4:
            if c and (int(a), int(b), int(c)) == (d.year, d.month, d.day):
                return True
            continue
        if not c and not (sep == "/" or (sep == "." and t[m.end():m.end() + 1] == ".")):
            continue   # "1-7" or "1.5" without a year is a range or a number, not a date
        if c and int(c) not in (d.year, d.year % 100):
            continue
        if (int(a), int(b)) in ((d.day, d.month), (d.month, d.day)):
            return True
    return False
