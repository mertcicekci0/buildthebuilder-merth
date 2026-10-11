"""Check a running mock against every answer in test-customers.json (status + body).

Usage: python3 -I scripts/verify.py "<case folder>/test-environment/test-customers.json" http://localhost:8799
Also checks synthesized errors (unknown customer/card, current week, empty fields) and the ledger.
"""
import json
import sys
import time
import urllib.error
import urllib.request

src, base = sys.argv[1], sys.argv[2].rstrip("/")
pack = json.load(open(src, encoding="utf-8"))
fails, checks = [], 0


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode(), time.time() - t
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), time.time() - t


def expect(label, svc, data, entry):
    global checks
    checks += 1
    st, text, dt = call("POST", "/voice/v1/" + svc, {"data": data, "call": {"ani": "5550000000", "lang": "tr"}})
    want_text = json.dumps(entry["response"], ensure_ascii=False)
    ok = st == entry["http"] and json.loads(text) == entry["response"] and text == want_text
    if "delaySeconds" in entry and dt < entry["delaySeconds"]:
        ok = False
    if not ok:
        fails.append((label, svc, st, entry["http"], text[:200]))
    return dt


def expect_err(label, svc, data, http, code):
    global checks
    checks += 1
    st, text, _ = call("POST", "/voice/v1/" + svc, {"data": data})
    b = json.loads(text)
    r = b.get("result", {})
    ok = st == http and "data" not in b and r.get("code") == code == r.get("returnCode") and r.get("message") == r.get("messageText") and r["details"]["guid"] == r["guid"]
    if not ok:
        fails.append((label, svc, st, http, text[:200]))


call("POST", "/__mock/reset")
for cu in pack["customers"]:
    no = cu["customerNo"]
    s = cu["services"]
    # customerNo as string for list, number for contact (both allowed)
    expect(no, "cards.list", {"customerNo": no, "aiFlow": "CardOps"}, s["cards.list"])
    expect(no, "contact.email.get", {"customerNo": int(no), "intAddressInfoType": "E"}, s["contact.email.get"])
    for card, e in s.get("cards.balance", {}).items():
        dt = expect(f"{no} {card}", "cards.balance", {"cardNumber": card}, e)
        if "delaySeconds" in e:
            print(f"delay check {card}: answered after {dt:.1f}s (delaySeconds={e['delaySeconds']})")
    for card, e in s.get("statement.week.get", {}).items():
        expect(f"{no} {card}", "statement.week.get", {"cardNumber": card}, e)
    for kind in ("statement.week.decide", "statement.week.change"):
        for card, weeks in s.get(kind, {}).items():
            cur = s["statement.week.get"][card]["response"].get("data", {}).get("statementWeek")
            for w, e in weeks.items():
                expect(f"{no} {card} w{w}", kind, {"cardNumber": card, "newStatementWeek": int(w), "statementDay": 5}, e)
                expect(f"{no} {card} w'{w}'", kind, {"cardNumber": card, "newStatementWeek": w, "statementDay": 5}, e)
            if cur is not None:
                expect_err(f"{no} {card} current week", kind, {"cardNumber": card, "newStatementWeek": cur, "statementDay": 5}, 400, "INVALID_ARGUMENTS_ERROR")
    pdf_req = {"customerNo": no, "cardNumber": "540000******0000", "currencyType": "TL", "statementDate": "05.10.2026", "emailAddress": "x@freyabank.example"}
    if "statement.pdf.send" in s:
        expect(no, "statement.pdf.send", pdf_req, s["statement.pdf.send"])
    else:
        expect_err(f"{no} no pdf entry", "statement.pdf.send", pdf_req, 404, "FB_IVR_CHANNEL_107")

# Unknown customer / card fallbacks (contract section 6)
expect_err("unknown customer", "cards.list", {"customerNo": "11111111"}, 404, "FB_IVR_CHANNEL_102")
expect_err("unknown card", "cards.balance", {"cardNumber": "540000******9999"}, 404, "FB_IVR_CHANNEL_120")
expect_err("unknown card", "statement.week.get", {"cardNumber": "540000******9999"}, 404, "FB_IVR_CHANNEL_121")
expect_err("unknown card", "statement.week.decide", {"cardNumber": "540000******9999", "newStatementWeek": 2}, 400, "INVALID_ARGUMENTS_ERROR")
expect_err("unknown card", "statement.week.change", {"cardNumber": "540000******9999", "newStatementWeek": 2}, 400, "INVALID_ARGUMENTS_ERROR")
expect_err("unknown customer", "contact.email.get", {"customerNo": "11111111", "intAddressInfoType": "E"}, 400, "INVALID_ARGUMENTS_ERROR")
expect_err("unknown customer", "statement.pdf.send", {"customerNo": "11111111", "cardNumber": "x", "currencyType": "TL", "statementDate": "05.10.2026", "emailAddress": "a@b.c"}, 404, "FB_IVR_CHANNEL_107")
# Empty / invalid fields
expect_err("empty", "cards.list", {"customerNo": ""}, 400, "INVALID_ARGUMENTS_ERROR")
expect_err("empty", "cards.balance", {"cardNumber": ""}, 400, "INVALID_ARGUMENTS_ERROR")
expect_err("empty", "statement.week.get", {}, 400, "INVALID_ARGUMENTS_ERROR")
expect_err("bad week", "statement.week.decide", {"cardNumber": "540000******1234", "newStatementWeek": 5}, 400, "INVALID_ARGUMENTS_ERROR")
expect_err("empty type", "contact.email.get", {"customerNo": "90000001", "intAddressInfoType": ""}, 400, "INVALID_ARGUMENTS_ERROR")
expect_err("empty email", "statement.pdf.send", {"customerNo": "90000001", "cardNumber": "540000******1234", "currencyType": "TL", "statementDate": "05.10.2026", "emailAddress": ""}, 400, "INVALID_ARGUMENTS_ERROR")

# Ledger: writes from above (incl. the rejected current-week change) are recorded per customer
st, text, _ = call("GET", "/__mock/ledger?customer=90000001")
led = json.loads(text)
services = [e["service"] for e in led["entries"]]
checks += 1
if not (st == 200 and services.count("statement.week.change") == 7 and services.count("statement.pdf.send") == 2):
    fails.append(("ledger 90000001", services))
print(f"ledger 90000001: {len(services)} entries {sorted(set(services))}")

print(f"{checks} checks, {len(fails)} failures")
for f in fails:
    print("FAIL", f)
sys.exit(1 if fails else 0)
