"""Practice-bank helper: open a practice session, call a bank tool directly, read a ledger.

    .venv/bin/python devtools/bank.py session PUB-01
    .venv/bin/python devtools/bank.py tool verify_identity '{"customer_id": "c_...", "field": "phone_last4", "answer": "4471"}'
    .venv/bin/python devtools/bank.py ledger c_...

Practice endpoints need FREYA_BANK_TEAM_KEY. The seven tool endpoints need only the customer_id.
"""
import json
import os
import sys
import urllib.error
import urllib.request

HOST = os.environ.get("FREYA_BANK_HOST", "https://freya-bank-mock.freyavoice.workers.dev")
TOOL_BASE = HOST + "/v1"
UA = "Mozilla/5.0 (freya-builder-devtools)"


def _req(url, body=None, team_key=False):
    h = {"user-agent": UA, "content-type": "application/json"}
    if team_key:
        h["x-team-key"] = os.environ["FREYA_BANK_TEAM_KEY"]
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers=h, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode("utf-8", "replace")[:500]


def session(case_id):
    return _req(HOST + "/v1/practice/sessions", {"case": case_id}, team_key=True)


def ledger(customer_id):
    return _req(HOST + "/v1/practice/ledger/" + customer_id, team_key=True)


def tool(name, body, base=TOOL_BASE):
    return _req(f"{base}/{name}", body)


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    if cmd == "session":
        out = session(rest[0])
    elif cmd == "ledger":
        out = ledger(rest[0])
    elif cmd == "tool":
        out = tool(rest[0], json.loads(rest[1]))
    else:
        sys.exit(__doc__)
    print(json.dumps({"status": out[0], "body": out[1]}, indent=1))
