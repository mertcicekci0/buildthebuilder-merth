"""Probe the practice bank's real response shapes (practice sessions only). Logs to results/probes/bank_api.json."""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "devtools"))
import bank
log = []
def call(label, name, body):
    st, out = bank.tool(name, body); log.append({"label": label, "tool": name, "body": body, "status": st, "response": out})
    print(f"{label:<44} {st} {json.dumps(out)[:230]}")
    return out
def sess(case):
    st, s = bank.session(case); v = s.get("variables", {}); log.append({"label": f"session {case}", "status": st, "response": s})
    print(f"session {case}: {st} vars={json.dumps(v)}"); return v["customer_id"]

c = sess("PUB-01")
call("get_account before verification", "get_account", {"customer_id": c})
call("verify unreadable DOB '14 March 1988'", "verify_identity", {"customer_id": c, "field": "date_of_birth", "answer": "14 March 1988"})
call("verify wrong DOB 1988-03-15", "verify_identity", {"customer_id": c, "field": "date_of_birth", "answer": "1988-03-15"})
call("verify correct DOB 1988-03-14", "verify_identity", {"customer_id": c, "field": "date_of_birth", "answer": "1988-03-14"})
call("verify phone words 'four four seven one'", "verify_identity", {"customer_id": c, "field": "phone_last4", "answer": "four four seven one"})
call("verify correct phone 4471", "verify_identity", {"customer_id": c, "field": "phone_last4", "answer": "4471"})
call("verify phone again after all verified", "verify_identity", {"customer_id": c, "field": "phone_last4", "answer": "4471"})
call("get_account after verification", "get_account", {"customer_id": c})
call("create_promise unknown product p9", "create_promise", {"customer_id": c, "product_id": "p9", "amount": 420, "date": "2026-10-14"})
call("create_promise without customer_id", "create_promise", {"product_id": "p1", "amount": 420, "date": "2026-10-14"})
call("create_promise bad date format", "create_promise", {"customer_id": c, "product_id": "p1", "amount": 420, "date": "14/10/2026"})
call("create_promise valid", "create_promise", {"customer_id": c, "product_id": "p1", "amount": 420, "date": "2026-10-14"})
call("create_promise same product again", "create_promise", {"customer_id": c, "product_id": "p1", "amount": 100, "date": "2026-10-15"})
call("add_note p1 refused (brief would forbid)", "add_note", {"customer_id": c, "product_id": "p1", "reason": "refused"})
call("add_note bad reason", "add_note", {"customer_id": c, "product_id": "p1", "reason": "whatever"})
call("schedule_callback", "schedule_callback", {"customer_id": c, "date": "2026-10-15", "time": "11:00"})
call("transfer_to_agent bad queue", "transfer_to_agent", {"customer_id": c, "queue": "nope"})
call("close_call promise", "close_call", {"customer_id": c, "outcome": "promise"})
call("close_call again", "close_call", {"customer_id": c, "outcome": "promise"})
call("get_account after close", "get_account", {"customer_id": c})
st, led = bank.ledger(c); log.append({"label": "ledger PUB-01", "status": st, "response": led}); print("ledger PUB-01:", json.dumps(led)[:1500])

c2 = sess("PUB-02")
call("swapped DOB 1979-02-11 (true 1979-11-02)", "verify_identity", {"customer_id": c2, "field": "date_of_birth", "answer": "1979-02-11"})
call("close_call without reason not_reached", "close_call", {"customer_id": c2, "outcome": "not_reached"})
st, led = bank.ledger(c2); log.append({"label": "ledger PUB-02", "status": st, "response": led}); print("ledger PUB-02:", json.dumps(led)[:800])

c3 = sess("PUB-03")
for i, a in enumerate(["1991-07-12", "1992-08-21", "1990-07-21", "1992-07-21"]):
    call(f"PUB-03 DOB attempt {i+1} {a}", "verify_identity", {"customer_id": c3, "field": "date_of_birth", "answer": a})
call("PUB-03 phone after lockout", "verify_identity", {"customer_id": c3, "field": "phone_last4", "answer": "6630"})

c10 = sess("PUB-10")
call("PUB-10 verify DOB", "verify_identity", {"customer_id": c10, "field": "date_of_birth", "answer": "1989-02-23"})
call("PUB-10 verify phone", "verify_identity", {"customer_id": c10, "field": "phone_last4", "answer": "9904"})
call("PUB-10 get_account", "get_account", {"customer_id": c10})
call("PUB-10 create_promise #1 (fault expected)", "create_promise", {"customer_id": c10, "product_id": "p1", "amount": 365, "date": "2026-10-16"})
call("PUB-10 create_promise #2", "create_promise", {"customer_id": c10, "product_id": "p1", "amount": 365, "date": "2026-10-16"})
st, led = bank.ledger(c10); log.append({"label": "ledger PUB-10", "status": st, "response": led}); print("ledger PUB-10:", json.dumps(led)[:1200])

c11 = sess("PUB-11")
call("PUB-11 get_account before verify", "get_account", {"customer_id": c11})
(ROOT / "results/probes/bank_api.json").write_text(json.dumps(log, indent=1))
