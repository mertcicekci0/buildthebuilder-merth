# Phone-agent runtime. This file is embedded VERBATIM into Freya code nodes and tool hooks, and the same
# source is unit-tested locally. Rules: standard modules json/re/datetime only; never raise from helpers used in
# code nodes; never write two curly braces in a row (Freya templating). All policy values come from the
# config dict CFG (compiled from the brief), never from literals in this file.
import datetime
import json
import re

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]


# ---------------------------------------------------------------- dates
def parse_now(raw):
    s = str(raw or "").strip().replace(" ", "T").rstrip("Z")
    for fmt, n in (("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%dT%H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.datetime.strptime(s[:n], fmt)
        except ValueError:
            pass
    return None


def iso(d):
    return d.isoformat()


def spoken_date(d):
    return WEEKDAYS[d.weekday()].capitalize() + " " + str(d.day) + " " + MONTHS[d.month - 1].capitalize()


def holidays(cfg):
    out = set()
    for h in cfg["calendar"].get("holidays", []):
        try:
            out.add(datetime.date.fromisoformat(str(h)[:10]))
        except ValueError:
            pass
    return out


def is_business_day(d, cfg):
    return d.weekday() in cfg["calendar"].get("business_weekdays", [0, 1, 2, 3, 4]) and d not in holidays(cfg)


def next_business_day_on_or_after(d, cfg):
    for _ in range(400):
        if is_business_day(d, cfg):
            return d
        d = d + datetime.timedelta(days=1)
    return d


def add_business_days(d, n, cfg):
    """The n-th business day after d (d itself is not counted)."""
    cur, k = d, 0
    for _ in range(2000):
        if k >= n:
            return cur
        cur = cur + datetime.timedelta(days=1)
        if is_business_day(cur, cfg):
            k += 1
    return cur


def week_bounds(d, cfg):
    start = d - datetime.timedelta(days=(d.weekday() - cfg["calendar"].get("week_start", 0)) % 7)
    return start, start + datetime.timedelta(days=6)


def last_business_day_in(start, end, cfg):
    d = end
    while d >= start:
        if is_business_day(d, cfg):
            return d
        d = d - datetime.timedelta(days=1)
    return None


def end_of_week_offer(today, cfg):
    """Last business day of this week; if today is that day (or none is left this week), the last of next week."""
    start, end = week_bounds(today, cfg)
    d = last_business_day_in(today, end, cfg)
    if d is None or d <= today:
        d = last_business_day_in(start + datetime.timedelta(days=7), end + datetime.timedelta(days=7), cfg)
    return d


def weekday_in_week(today, wd, weeks_ahead, cfg):
    start, _ = week_bounds(today, cfg)
    off = (wd - cfg["calendar"].get("week_start", 0)) % 7
    return start + datetime.timedelta(days=7 * weeks_ahead + off)


def next_weekday_date(today, wd, cfg):
    """"next <weekday>": calendar.next_weekday "following_week" (default) = that weekday in the following calendar week;
    "next_occurrence" = the first such weekday after today."""
    if (cfg.get("calendar") or {}).get("next_weekday") == "next_occurrence":
        return today + datetime.timedelta(days=(wd - today.weekday() - 1) % 7 + 1)
    return weekday_in_week(today, wd, 1, cfg)


def band_for(days_late, cfg):
    """The band containing days_late. Outside every band a product still gets a band, so it always has a deadline:
    below the lowest band -> the lowest band; above the highest band (or in a gap between bands) -> the highest band
    that starts at or below days_late."""
    try:
        n = int(float(days_late))
    except (TypeError, ValueError):
        return None
    bands = sorted(cfg["bands"], key=lambda b: b["min"])
    for b in bands:
        if b["min"] <= n <= b["max"]:
            return b
    if not bands:
        return None
    below = [b for b in bands if b["min"] <= n]
    return below[-1] if below else bands[0]


def deadline_for(today, days_late, cfg):
    b = band_for(days_late, cfg)
    if not b:
        return None
    n = int(b.get("deadline_business_days") or 0)
    if b.get("deadline_unit") == "calendar_days":
        d = today + datetime.timedelta(days=n)          # latest promise date = last business day on or before that day
        while d > today and not is_business_day(d, cfg):
            d = d - datetime.timedelta(days=1)
        return d
    return add_business_days(today, n, cfg)


# ---------------------------------------------------------------- products
def money(x):
    try:
        v = round(float(x), 2)
    except (TypeError, ValueError):
        return str(x)
    s = ("%.2f" % v)
    if s.endswith(".00"):
        s = s[:-3]
    return s


def num(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


def load_products(vars, cfg):
    """Rebuild the product list from Freya's flattened keys <prefix>.<i>.<field> (verified: lists only arrive flattened)."""
    prefix = cfg["products_prefix"]
    raw = vars.get(cfg.get("products_var", "")) if cfg.get("products_var") else None
    items = []
    if isinstance(raw, list):
        items = [x for x in raw if isinstance(x, dict)]
    elif isinstance(raw, str) and raw.strip().startswith("["):
        try:
            items = [x for x in json.loads(raw) if isinstance(x, dict)]
        except ValueError:
            items = []
    if not items:
        by_index = {}
        for k, v in vars.items():
            if not isinstance(k, str) or not k.startswith(prefix + "."):
                continue
            rest = k[len(prefix) + 1:].split(".", 1)
            if len(rest) == 2 and rest[0].isdigit():
                by_index.setdefault(int(rest[0]), {})[rest[1]] = v
        items = [by_index[i] for i in sorted(by_index)]
    return items


def product_facts(items, today, cfg):
    f = cfg["fields"]
    out = []
    for i, it in enumerate(items):
        ptype = str(it.get(f["product_type"], ""))
        pcfg = cfg["product_types"].get(ptype) or cfg["product_types"].get("*") or {}
        floor_field = pcfg.get("floor_field")
        full = num(it.get(f["past_due"]))
        floor = num(it.get(floor_field)) if floor_field else None
        days_late = it.get(f["days_late"])
        band = band_for(days_late, cfg) or {}
        deadline = deadline_for(today, days_late, cfg)
        spoken = pcfg.get("spoken", ptype.replace("_", " "))
        for key, val in it.items():
            spoken = spoken.replace("<" + str(key) + ">", str(val))
        a1 = end_of_week_offer(today, cfg)
        if a1 and deadline and a1 > deadline:   # a short deadline caps every proposed date
            a1 = deadline
        out.append({"index": i, "product_id": str(it.get(f["product_id"], "p%d" % (i + 1))), "type": ptype, "spoken": spoken,
                    "full": full, "floor": floor, "days_late": days_late, "band": band.get("name", ""),
                    "attempts": int(band.get("attempts", 0) or 0), "deadline": iso(deadline) if deadline else "",
                    "attempt1_date": iso(a1) if a1 else ""})
    return out


# ---------------------------------------------------------------- decisions
def decide_date(proposed, prod, today, cfg):
    """Verdict on a date the customer proposes for one product: (verdict, date_to_offer_or_record)."""
    dl = datetime.date.fromisoformat(prod["deadline"]) if prod.get("deadline") else None
    if proposed < today:
        return "past", None
    if dl and proposed > dl:
        return "after_deadline", dl
    if not is_business_day(proposed, cfg):
        nxt = next_business_day_on_or_after(proposed, cfg)
        if dl and nxt > dl:
            return "after_deadline", dl
        return "not_business_day", nxt
    return "ok", proposed


def decide_amount(amount, prod):
    a, full, floor = num(amount), prod.get("full"), prod.get("floor")
    if a is None or full is None:
        return "unknown"
    if a > full + 0.004:
        return "above_full"
    if floor is not None and a < floor - 0.004:
        return "below_floor"
    return "ok"


def date_table(today, prods, cfg, days=None):
    """One line per calendar day from today up to the latest deadline + a margin: what the agent may do with it."""
    days = days or int(cfg.get("table_days", 45))
    rows = []
    for k in range(days + 1):
        d = today + datetime.timedelta(days=k)
        tags = []
        if k == 0:
            tags.append("today")
        if k == 1:
            tags.append("tomorrow")
        if d in holidays(cfg):
            tags.append("bank holiday")
        elif not is_business_day(d, cfg):
            tags.append("not a business day")
        per = []
        for p in prods:
            v, alt = decide_date(d, p, today, cfg)
            if v == "ok":
                per.append(p["product_id"] + " ok")
            elif v == "not_business_day":
                per.append(p["product_id"] + " offer " + spoken_date(alt) + " instead")
            elif v == "after_deadline":
                per.append(p["product_id"] + " too late, latest " + spoken_date(alt))
        rows.append(iso(d) + " " + spoken_date(d) + (" (" + ", ".join(tags) + ")" if tags else "") + ": " + "; ".join(per))
    return "\n".join(rows)


def relative_dates_text(today, cfg):
    lines = []
    for wd in range(7):
        this_d = weekday_in_week(today, wd, 0, cfg)
        next_d = next_weekday_date(today, wd, cfg)
        name = WEEKDAYS[wd]
        lines.append("this " + name + " = " + spoken_date(this_d) + (" (already past)" if this_d < today else "") +
                     "; next " + name + " = " + spoken_date(next_d))
    return "\n".join(lines)


# ---------------------------------------------------------------- locale (used only when CFG has "locale": a non-English brief)
def loc_spoken_date(d, cfg):
    loc = cfg.get("locale") or {}
    wd = loc.get("weekday_names") or []
    mo = loc.get("month_names") or []
    wd = wd[d.weekday()] if len(wd) == 7 else WEEKDAYS[d.weekday()].capitalize()
    mo = mo[d.month - 1] if len(mo) == 12 else MONTHS[d.month - 1].capitalize()
    pat = loc.get("date_pattern") or "<weekday> <day> <month>"
    return pat.replace("<weekday>", str(wd)).replace("<day>", str(d.day)).replace("<month>", str(mo)).strip()


def loc_money(x, cfg):
    loc = cfg.get("locale") or {}
    try:
        v = round(float(x), 2)
    except (TypeError, ValueError):
        return str(x)
    s = "%.2f" % abs(v)
    whole, frac = s.split(".")
    sep = loc.get("thousands_separator") or ""
    if sep:
        groups = []
        while len(whole) > 3:
            groups.insert(0, whole[-3:])
            whole = whole[:-3]
        whole = sep.join([whole] + groups)
    out = whole if frac == "00" else whole + (loc.get("decimal_separator") or ".") + frac
    return ("-" if v < 0 else "") + out


_FOLD_FROM = "\u00e0\u00e1\u00e2\u00e3\u00e4\u00e5\u0101\u0103\u0105\u00e7\u0107\u010d\u00e8\u00e9\u00ea\u00eb\u0113\u0117\u0119\u011b\u011f\u00ec\u00ed\u00ee\u00ef\u0131\u012b\u0142\u00f1\u0144\u0148\u00f2\u00f3\u00f4\u00f5\u00f6\u00f8\u014d\u0151\u0159\u015b\u015f\u0161\u0163\u0165\u00f9\u00fa\u00fb\u00fc\u016b\u016f\u0171\u00fd\u00ff\u017a\u017c\u017e"
_FOLD_TO = "aaaaaaaaaccceeeeeeeegiiiiiilnnnoooooooorsssttuuuuuuuyyzzz"


def fold(text):
    """Lowercase without diacritics, so a transcript with or without accents (and either dotted or dotless i) matches."""
    s = str(text).replace("\u0130", "i").lower().replace("\u0307", "")
    return s.translate(str.maketrans(_FOLD_FROM, _FOLD_TO))


def loc_words(text, cfg):
    """Customer words in the brief's language -> the English tokens the parsers read (month, weekday and relative-day words)."""
    loc = cfg.get("locale") or {}
    s = " " + fold(text) + " "
    pairs = []
    for key, eng in (("month_names", MONTHS), ("weekday_names", WEEKDAYS)):
        names = loc.get(key) or []
        if len(names) == len(eng):
            pairs += [(fold(n).strip(), eng[i]) for i, n in enumerate(names)]
    for key, eng in (("day_after_tomorrow_words", "day after tomorrow"), ("today_words", "today"), ("tomorrow_words", "tomorrow"),
                     ("next_words", "next"), ("this_words", "this"), ("vague_words", "soon")):
        pairs += [(fold(n).strip(), eng) for n in (loc.get(key) or [])]
    for name, eng in sorted(pairs, key=lambda x: -len(x[0])):
        if name:
            tail = r"\w*" if len(name) >= 4 else r"(?!\w)"   # long names keep suffixes ("cumaya"); short words match whole
            s = re.sub(r"(?<!\w)" + re.escape(name) + tail, " " + eng + " ", s)
    return re.sub(r"\s+", " ", s)


def loc_resolve_day_words(words, today, cfg):
    return resolve_day_words(loc_words(words, cfg), today, cfg)


def loc_normalize_answer(fmt, text, order="DMY", cfg=None):
    if fmt == "date" and cfg:
        return normalize_answer(fmt, loc_words(text, cfg).strip(), order)
    return normalize_answer(fmt, text, order)


def callback_check(date_iso, time_hhmm, now, cfg):
    cb = cfg.get("callback") or {}
    try:
        d = datetime.date.fromisoformat(str(date_iso)[:10])
        hh, mm = [int(x) for x in str(time_hhmm).strip()[:5].split(":")]
        t = datetime.time(hh, mm)
    except (ValueError, TypeError):
        return "unreadable"
    today = now.date()
    allowed = []
    for rule in cb.get("days", []):
        if rule == "today":
            allowed.append(today)
        elif rule == "next_business_day":
            allowed.append(add_business_days(today, 1, cfg))
        elif rule.startswith("within_business_days:"):
            n = int(rule.split(":")[1])
            allowed += [add_business_days(today, i, cfg) for i in range(1, n + 1)]
    if cb.get("business_days_only") and not is_business_day(d, cfg):
        return "not_business_day"
    if d not in allowed:
        return "day_not_allowed"
    start = datetime.time(*[int(x) for x in cb.get("start", "00:00").split(":")])
    end = datetime.time(*[int(x) for x in cb.get("end", "23:59").split(":")])
    if t < start or t > end:
        return "time_not_allowed"
    if cb.get("after_now", True) and datetime.datetime.combine(d, t) <= now:
        return "not_after_now"
    return "ok"


# ---------------------------------------------------------------- answers
_UNITS = {"zero": 0, "oh": 0, "o": 0, "nought": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def words_to_digits(text):
    """'four four seven one' -> '4471'; 'forty-four seventy-one' -> '4471'; digits pass through."""
    toks = re.findall(r"[a-z]+|\d+", str(text).lower().replace("-", " "))
    out, i = "", 0
    while i < len(toks):
        t = toks[i]
        if t.isdigit():
            out += t
        elif t in ("double", "triple") and i + 1 < len(toks) and toks[i + 1] in _UNITS and _UNITS[toks[i + 1]] < 10:
            out += str(_UNITS[toks[i + 1]]) * (2 if t == "double" else 3)
            i += 1
        elif t in _TENS:
            v = _TENS[t]
            if i + 1 < len(toks) and toks[i + 1] in _UNITS and 0 < _UNITS[toks[i + 1]] < 10:
                v += _UNITS[toks[i + 1]]
                i += 1
            out += str(v)
        elif t in _UNITS:
            out += str(_UNITS[t])
        elif t == "hundred" and out:
            out += "00"
        i += 1
    return out


def normalize_date_answer(text, order="DMY"):
    s = str(text).strip().lower()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        month = None
        for i, name in enumerate(MONTHS):
            if re.search(r"\b" + name[:3] + r"[a-z]*\b", s):
                month = i + 1
        nums = [int(x) for x in re.findall(r"\d+", s)]
        if month and len(nums) >= 2:
            year = next((n for n in nums if n > 31), None)
            day = next((n for n in nums if n <= 31 and n != year), None)
            if year is None or day is None:
                return str(text)
            y, mo, d = year, month, day
        elif len(nums) == 3:
            a, b, c = nums
            if a > 31:
                y, mo, d = a, b, c
            elif order == "MDY":
                mo, d, y = a, b, c
            else:
                d, mo, y = a, b, c
        else:
            return str(text)
    if y < 100:
        y += 1900 if y > 30 else 2000
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return str(text)


def normalize_answer(fmt, text, order="DMY"):
    if fmt == "date":
        return normalize_date_answer(text, "MDY" if str(order).upper() == "MDY" else "DMY")
    if fmt.startswith("digits"):
        digits = words_to_digits(text)
        return digits or str(text)
    if fmt == "upper_nospace":
        spoken = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
        return "".join(spoken.get(t.lower(), t) for t in re.findall(r"[^\W\d_]+|\d+", str(text))).upper()
    return str(text).strip()


# ---------------------------------------------------------------- state
def get_state(vars):
    """Shared state = st_json (written only by sequential code nodes) overlaid with per-key entries that tools may
    write concurrently (verified 2026-10-11: parallel tool calls each see a snapshot; writes merge per variable)."""
    st = None
    try:
        st = json.loads(vars.get("st_json") or "")
    except (TypeError, ValueError):
        st = None
    if not isinstance(st, dict):
        st = {"products": {}, "promises": {}, "notes": {}, "events": [], "transfer": "", "verified": "", "contact": ""}
    for key, default in (("promises", {}), ("notes", {}), ("products", {}), ("order", []), ("transfer", ""), ("verified", ""), ("contact", "")):
        st.setdefault(key, default)
    for k, v in list(vars.items()):
        if not isinstance(k, str) or not v:
            continue
        if k.startswith("pr__"):
            try:
                st["promises"][k[4:]] = json.loads(v)
            except (TypeError, ValueError):
                pass
        elif k.startswith("oc__"):
            st["notes"][k[4:]] = str(v)
        elif k == "tr__":
            st["transfer"] = str(v)
    for pid in st["promises"]:
        st["notes"].pop(pid, None)
    return st


def put_state(vars, st):
    keep = dict((k, v) for k, v in st.items() if k not in ("promises",))
    vars["st_json"] = json.dumps(keep, sort_keys=True)


def resolve_day_words(words, today, cfg):
    """Deterministic reading of a spoken day relative to today. Returns (date or None, kind)."""
    w = " " + re.sub(r"[^a-z0-9 ]", " ", str(words).lower()) + " "
    w = re.sub(r"\s+", " ", w)
    vague = ["next week", "end of the month", "end of month", "salary", "payday", "soon", "later", "some time", "sometime", "beginning of", "middle of", "few days", "couple of"]
    if " today " in w:
        return today, "today"
    if "day after tomorrow" in w:
        return today + datetime.timedelta(days=2), "relative"
    if " tomorrow " in w:
        return today + datetime.timedelta(days=1), "relative"
    ordinals = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
                "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
                "eighteenth": 18, "nineteenth": 19, "twentieth": 20, "thirtieth": 30}
    day = None
    m = re.search(r" (\d{1,2})(st|nd|rd|th)? ", w)
    if m and 1 <= int(m.group(1)) <= 31:
        day = int(m.group(1))
    else:
        m2 = re.search(r" (twenty|thirty)?\s?(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|thirtieth) ", w)
        if m2:
            day = ordinals[m2.group(2)] + (20 if m2.group(1) == "twenty" else 30 if m2.group(1) == "thirty" else 0)
    month = None
    for i, name in enumerate(MONTHS):
        if " " + name + " " in w or " " + name[:3] + " " in w:
            month = i + 1
    wd = None
    for i, name in enumerate(WEEKDAYS):
        if " " + name + " " in w:
            wd = i
    if day:
        if month:
            y = today.year if (month, day) >= (today.month, today.day) else today.year + 1
            try:
                return datetime.date(y, month, day), "explicit"
            except ValueError:
                return None, "invalid"
        for k in range(0, 62):
            d = today + datetime.timedelta(days=k)
            if d.day == day:
                return d, "explicit"
    if wd is not None:
        if " next " in w:
            return next_weekday_date(today, wd, cfg), "next_weekday"
        d = weekday_in_week(today, wd, 0, cfg)
        if " this " in w or d >= today:
            return d, "this_weekday"
        return weekday_in_week(today, wd, 1, cfg), "weekday"
    if any(v in w for v in vague) or " week " in w or " month " in w:
        return None, "vague"
    return None, "unknown"


def outcome_fold(st, cfg):
    """(outcome, reason) from the recorded state; table names come from cfg['outcomes']."""
    o = cfg["outcomes"]
    if st.get("transfer"):
        return o["transferred"], (o.get("transfer_reasons") or {}).get(st["transfer"], st["transfer"])
    if st.get("contact") and st.get("contact") != "customer":
        return o["not_reached"], st["contact"]
    if st.get("verified") in ("failed", "declined"):
        return o["not_verified"], (o.get("not_verified_reasons") or {}).get(st["verified"], st["verified"])
    if st.get("promises"):
        return o["promise"], ""
    order = st.get("order") or sorted(st.get("notes", {}))
    reasons = [st.get("notes", {}).get(pid) for pid in order if st.get("notes", {}).get(pid)]
    if reasons and all(r == o.get("paid_note", "paid") for r in reasons):
        return o["paid"], "paid"   # mapped by the close hook; most briefs list no reason for paid ("" -> none sent)
    for r in reasons:
        if r != o.get("paid_note", "paid"):
            return o["no_promise"], r
    if st.get("end_reason"):
        return o["no_promise"], st["end_reason"]
    if not st.get("contact"):
        return o["not_reached"], "unclear"          # the call ended before anyone was confirmed
    if st.get("verified") != "yes":
        return o["not_verified"], "declined"        # the customer left during the identity check
    return o.get("fallback_outcome", o["no_promise"]), o.get("fallback_reason") or "refused"   # never an empty no_promise reason
