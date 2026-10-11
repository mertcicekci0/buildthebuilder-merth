import datetime as dt, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder import runtime_src as rt

CFG = json.load(open(ROOT / "tests/fixtures/runtime_cfg_public_ab.json"))
CASES = {c["id"]: c for c in json.load(open(ROOT / "kit/public-test-set.json"))}
D = dt.date.fromisoformat
TODAY = D("2026-10-14")

def facts(case_id, now=None):
    c = CASES[case_id]
    items = [dict(p, product_id=f"p{i+1}") for i, p in enumerate(c["products"])]
    return rt.product_facts(items, rt.parse_now(now or c["variables"]["now"]).date(), CFG)

def test_parse_now_variants():
    assert rt.parse_now("2026-10-14T11:00") == dt.datetime(2026, 10, 14, 11, 0)
    assert rt.parse_now("2026-10-14 11:00:30Z") == dt.datetime(2026, 10, 14, 11, 0, 30)
    assert rt.parse_now("2026-10-14").date() == TODAY and rt.parse_now("garbage") is None

def test_reference_deadlines_and_offers():
    assert rt.deadline_for(TODAY, 5, CFG) == D("2026-11-02")      # early: 12th business day, skips Oct 26 holiday
    assert rt.deadline_for(TODAY, 14, CFG) == D("2026-10-23")     # late: 7th business day
    assert rt.end_of_week_offer(TODAY, CFG) == D("2026-10-16")
    assert rt.end_of_week_offer(D("2026-10-16"), CFG) == D("2026-10-23")   # today is the last business day
    assert rt.weekday_in_week(D("2026-10-16"), 4, 1, CFG) == D("2026-10-23")  # "next Friday" said on a Friday

def test_every_public_expected_promise_is_valid_and_derivable():
    for cid, c in CASES.items():
        fs = {f["product_id"]: f for f in facts(cid)}
        for p in c["expected"]["promises"]:
            f = fs[p["product_id"]]
            assert rt.decide_date(D(p["date"]), f, TODAY, CFG) == ("ok", D(p["date"])), (cid, p)
            assert rt.decide_amount(p["amount"], f) == "ok", (cid, p)

def test_public_case_specific_decisions():
    f04 = facts("PUB-04")[0]; assert rt.decide_date(D("2026-11-06"), f04, TODAY, CFG) == ("after_deadline", D("2026-10-23"))
    f07 = facts("PUB-07")[0]; assert rt.decide_date(D("2026-10-26"), f07, TODAY, CFG) == ("not_business_day", D("2026-10-27"))
    f13 = facts("PUB-13"); assert rt.decide_date(D("2026-10-30"), f13[0], TODAY, CFG)[0] == "ok"
    assert rt.decide_date(D("2026-10-30"), f13[1], TODAY, CFG) == ("after_deadline", D("2026-10-23"))
    f06 = facts("PUB-06")[0]; assert (f06["floor"], f06["attempts"], f06["attempt1_date"]) == (637.5, 2, "2026-10-16")
    assert rt.decide_amount(600, f06) == "below_floor" and rt.decide_amount(2125.1, f06) == "ok" and rt.decide_amount(3000, f06) == "above_full"
    f12 = facts("PUB-12"); assert [f["attempts"] for f in f12] == [2, 2, 1]
    assert f12[0]["spoken"] == "credit card ending 9015"

def test_other_calendar_days():
    assert rt.end_of_week_offer(D("2026-12-24"), CFG) == D("2026-12-31")     # Thu Dec 24: Fri 25 is a holiday
    assert rt.end_of_week_offer(D("2026-10-17"), CFG) == D("2026-10-23")     # Saturday: nothing left this week
    assert rt.deadline_for(D("2026-12-24"), 3, CFG) == D("2027-01-13")       # skips Dec 25 and Jan 1
    assert rt.decide_date(D("2026-10-13"), facts("PUB-01")[0], TODAY, CFG)[0] == "past"

def test_callback_window():
    now = rt.parse_now("2026-10-14T11:00")
    assert rt.callback_check("2026-10-15", "11:00", now, CFG) == "ok"
    assert rt.callback_check("2026-10-14", "10:30", now, CFG) == "not_after_now"
    assert rt.callback_check("2026-10-14", "19:00", now, CFG) == "ok"        # both ends included
    assert rt.callback_check("2026-10-15", "19:01", now, CFG) == "time_not_allowed"
    assert rt.callback_check("2026-10-16", "11:00", now, CFG) == "day_not_allowed"
    fri = rt.parse_now("2026-10-23T18:00")                                    # next business day skips weekend + Oct 26 holiday
    assert rt.callback_check("2026-10-27", "10:00", fri, CFG) == "ok"

def test_answer_normalisation():
    assert rt.normalize_answer("digits4", "four four seven one") == "4471"
    assert rt.normalize_answer("digits4", "forty-four seventy-one") == "4471"
    assert rt.normalize_answer("digits4", "double four seven one") == "4471"
    assert rt.normalize_answer("digits4", "4 4 7 1") == "4471"
    assert rt.normalize_answer("date", "14/03/1988") == "1988-03-14"
    assert rt.normalize_answer("date", "14 March 1988") == "1988-03-14"
    assert rt.normalize_answer("date", "March 14th, 1988") == "1988-03-14"
    assert rt.normalize_answer("date", "1988-03-14") == "1988-03-14"

def test_date_table_mentions_holiday_and_deadlines():
    t = rt.date_table(TODAY, facts("PUB-13"), CFG, days=20)
    assert "2026-10-26 Monday 26 October (bank holiday): p1 offer Tuesday 27 October instead; p2 too late, latest Friday 23 October" in t
    assert t.splitlines()[0].startswith("2026-10-14 Wednesday 14 October (today): p1 ok; p2 ok")

def test_outcome_fold():
    o = lambda **st: rt.outcome_fold(dict({"products": {}, "promises": {}, "notes": {}, "transfer": "", "verified": "", "contact": "customer"}, **st), CFG)
    assert o(promises={"p1": 1}) == ("promise", "")
    assert o(promises={"p1": 1}, transfer="general") == ("transferred", "human")
    assert o(notes={"p1": "paid", "p2": "paid"}, order=["p1", "p2"]) == ("paid", "paid")   # semantic; the close hook maps it ("" -> no reason sent)
    assert o(notes={"p1": "paid", "p2": "dispute", "p3": "refused"}, order=["p1", "p2", "p3"]) == ("no_promise", "dispute")
    assert o(contact="callback") == ("not_reached", "callback")
    assert o(verified="failed") == ("not_verified", "failed")

def test_load_products_from_flattened_keys():
    vars = {"acct_response.data.products.0.product_id": "p1", "acct_response.data.products.0.past_due": 365,
            "acct_response.data.products.1.product_id": "p2", "acct_response.data.ok": "true"}
    assert [p["product_id"] for p in rt.load_products(vars, CFG)] == ["p1", "p2"]

def test_source_is_freya_safe():
    src = (ROOT / "builder/runtime_src.py").read_text()
    assert "{{" not in src and "}}" not in src
    assert set(__import__("re").findall(r"^import (\w+)", src, __import__("re").M)) <= {"json", "re", "datetime"}

def test_resolve_day_words():
    r = lambda w, today=TODAY: rt.resolve_day_words(w, today, CFG)
    assert r("Monday the twenty sixth") == (D("2026-10-26"), "explicit")
    assert r("the 26th") == (D("2026-10-26"), "explicit")
    assert r("6 November") == (D("2026-11-06"), "explicit")
    assert r("this Friday") == (D("2026-10-16"), "this_weekday")
    assert r("next Friday") == (D("2026-10-23"), "next_weekday")
    assert r("next Friday", D("2026-10-16")) == (D("2026-10-23"), "next_weekday")   # said on a Friday
    assert r("Friday", D("2026-10-17")) == (D("2026-10-23"), "weekday")             # bare weekday already past -> next one
    assert r("tomorrow") == (D("2026-10-15"), "relative")
    assert r("the week after next")[1] == "vague" and r("when my salary comes")[1] == "vague"
    assert r("on the thirtieth") == (D("2026-10-30"), "explicit")

def test_parallel_safe_state_overlay():
    vars = {"st_json": '{"order": ["p1", "p2"], "notes": {"p1": "refused"}}', "pr__p1": "[220, \"2026-10-16\"]", "oc__p2": "dispute", "tr__": ""}
    st = rt.get_state(vars)
    assert st["promises"] == {"p1": [220, "2026-10-16"]} and st["notes"] == {"p2": "dispute"} and st["transfer"] == ""
