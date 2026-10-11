"""A non-English brief yields an agent that speaks only its language: the system's own spoken sentences come from
IR.phrases, dates and amounts are formatted with IR.locale, and customer day words are read with IR.locale's tables.
The IR below is SYNTHETIC (public IR + made-up Turkish phrases and locale values written here); English IRs are unchanged
(tests/test_golden.py)."""
import copy, datetime, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from builder import openapi_tools as ot, compile_workflow as cw, validate as v, local_exec as le, policy_schema as ps
from builder import runtime_src as rt

IR = {k: x for k, x in json.load(open(ROOT / "tests/fixtures/ir_public_ab.json")).items() if not k.startswith("_")}
API = json.load(open(ROOT / "kit/tools/openapi.json"))

PHRASES = {"verified": "Tesekkurler, kimliginiz dogrulandi.", "promise_recorded": "Odeme sozunuz kaydedildi: <list>.",
           "promises_recorded": "Odeme sozleriniz kaydedildi: <list>.", "promise_item": "<date> tarihinde <product> icin <amount>",
           "record_failed_for": "Uzgunum, <products> icin sozu kaydedemedim.", "and": "ve", "record_failed": "Sozu kaydedemedim.",
           "connecting": "Sizi simdi aktariyorum.", "goodbye": "Hosca kalin."}
LOCALE = {"month_names": ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"],
          "weekday_names": ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"],
          "date_pattern": "<day> <month> <weekday>", "decimal_separator": ",", "thousands_separator": ".",
          "today_words": ["bugün"], "tomorrow_words": ["yarın"], "day_after_tomorrow_words": ["öbür gün"],
          "next_words": ["haftaya", "gelecek"], "this_words": ["bu"]}


def _ir_tr():
    ir = copy.deepcopy(IR)
    ir.update(language="Turkish", currency_word="TL", phrases=dict(PHRASES), locale=copy.deepcopy(LOCALE))
    ir["fixed_sentences"]["farewell"] = "Iyi gunler dileriz."
    return ir


def _build(ir):
    api = ot.load(API); roles, probs = ot.infer_roles(api)
    assert probs == []
    wf = cw.compile_workflow(ir, api, roles)
    assert v.lint(wf, api) == []
    return api, roles, wf


def _spoken(n):
    if (n.get("on_enter") or {}).get("type") == "tts":
        yield n["on_enter"]["text"]
    msg = (((n.get("terminal") or {}).get("function") or {}).get("parameters") or {}).get("properties", {}).get("spoken_message")
    if msg:
        yield msg["default"]


def test_schema_has_phrases_and_locale_and_checks_name_counts():
    s = ps.schema()["properties"]
    assert set(s["phrases"]["properties"]) == set(ps.PHRASE_KEYS) == set(cw.PHRASE_DEFAULTS)
    assert "month_names" in s["locale"]["properties"]
    ir = _ir_tr(); ir["locale"]["month_names"] = ir["locale"]["month_names"][:11]
    api = ot.load(API); roles, _ = ot.infer_roles(api)
    assert any("locale.month_names" in p for p in ps.check(ir, api, roles))


def test_no_english_system_sentence_is_spoken():
    _, _, wf = _build(_ir_tr())
    spoken = [t for n in wf["nodes"].values() for t in _spoken(n)]
    joined = " | ".join(spoken)
    for eng in cw.PHRASE_DEFAULTS.values():
        if len(eng) > 4:
            assert eng.split(":")[0].replace("<", "") not in joined, eng
    assert "Thank you, you are verified" not in joined and "connecting you" not in joined and "Goodbye." not in joined
    assert wf["nodes"]["negotiate"]["on_enter"]["text"].startswith(PHRASES["verified"])
    assert any(t.startswith("Sizi simdi aktariyorum. ") for t in spoken)
    assert "Always speak Turkish" in json.dumps(wf)


def test_english_ir_keeps_english_even_with_phrases():
    ir = copy.deepcopy(IR); ir["phrases"] = dict(PHRASES); ir["locale"] = copy.deepcopy(LOCALE)
    _, _, wf_with = _build(ir)
    _, _, wf_plain = _build(copy.deepcopy(IR))
    assert json.dumps(wf_with, sort_keys=True) == json.dumps(wf_plain, sort_keys=True)


def test_record_summary_dates_and_amounts_in_the_locale():
    api, roles, wf = _build(_ir_tr())
    prods = [{"product_id": "p1", "type": "credit_card", "card_last4": "9015", "past_due": 1250.5, "days_late": 4, "minimum_payment": 90}]
    r = le.smoke(wf, roles, prods, promise=("p1", 1250.5, "2026-10-16"))
    assert r["record_summary"].startswith("Odeme sozunuz kaydedildi: 16 Ekim Cuma tarihinde credit card ending 9015 icin 1.250,50 TL.")
    assert "past due" not in r["debt_statement"] or IR.get("product_sentence")   # sentence text comes from the IR
    assert "1.250,50" in r["debt_statement"] and "Ekim" in r["facts_text"] and "October" not in r["facts_text"]


def test_check_date_reads_customer_words_with_the_locale_tables():
    _, _, wf = _build(_ir_tr())
    vars = {**wf["test_variables"], "now": "2026-10-14T11:00"}   # Wednesday
    le.run_code_node(wf, "init", vars)
    assert vars["today_spoken"] == "14 Ekim Çarşamba"
    vars.update(le.flatten_products("load_account_response.data.products",
                                    [{"product_id": "p1", "type": "credit_card", "card_last4": "9015", "past_due": 300, "days_late": 4, "minimum_payment": 90}]))
    le.run_code_node(wf, "account", vars)
    try:   # the model's own ISO date is wrong on purpose: the customer's words decide
        le.run_pre(le._tool(wf, "negotiate", "check_date"), vars, {"product_id": "p1", "customer_words": "yarın", "date": "2026-10-20"})
    except le.Blocked as b:
        msg = str(b)
    assert "15 Ekim Perşembe" in msg and "20 Ekim" not in msg


def test_runtime_locale_helpers():
    cfg = {"locale": LOCALE, "calendar": {"business_weekdays": [0, 1, 2, 3, 4], "holidays": [], "week_start": 0}}
    today = datetime.date(2026, 10, 14)   # Wednesday
    assert rt.loc_spoken_date(datetime.date(2026, 10, 19), cfg) == "19 Ekim Pazartesi"
    assert rt.loc_spoken_date(datetime.date(2026, 10, 19), {}) == rt.spoken_date(datetime.date(2026, 10, 19))
    assert rt.loc_money(1234567.5, cfg) == "1.234.567,50" and rt.loc_money(300, cfg) == "300" and rt.loc_money(300, {}) == rt.money(300)
    r = lambda w: rt.loc_resolve_day_words(w, today, cfg)
    assert r("bugün") == (today, "today")
    assert r("Yarın olur") == (datetime.date(2026, 10, 15), "relative")
    assert r("öbür gün") == (datetime.date(2026, 10, 16), "relative")
    assert r("cumaya") == (datetime.date(2026, 10, 16), "this_weekday")
    assert r("pazartesi")[0] == datetime.date(2026, 10, 19) and r("haftaya pazartesi")[1] == "next_weekday"
    assert r("cumartesi")[0] == datetime.date(2026, 10, 17)
    assert r("20 Ekim'de") == (datetime.date(2026, 10, 20), "explicit")
    assert r("3 Şubat") == (datetime.date(2027, 2, 3), "explicit")
    assert rt.loc_normalize_answer("date", "14 Mart 1988", "DMY", cfg) == "1988-03-14"
    assert rt.loc_normalize_answer("digits4", "4471", "DMY", cfg) == "4471"


def test_day_words_match_without_diacritics_or_in_capitals():
    cfg = {"locale": LOCALE, "calendar": {"business_weekdays": [0, 1, 2, 3, 4], "holidays": [], "week_start": 0}}
    today = datetime.date(2026, 10, 14)
    r = lambda w: rt.loc_resolve_day_words(w, today, cfg)
    assert r("yarin") == (datetime.date(2026, 10, 15), "relative")
    assert r("CARSAMBA")[0] == datetime.date(2026, 10, 14)
    assert r("5 KASIM") == (datetime.date(2026, 11, 5), "explicit")
    assert r("next friday") == rt.resolve_day_words("next friday", today, cfg)   # English words still read
