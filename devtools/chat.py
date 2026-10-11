"""Talk to a published workflow turn by turn (no simulation queue), then print the call timeline.

    .venv/bin/python devtools/chat.py <workflow_id> --case PUB-01 "Yes, speaking." "14 March 1988" ...
    .venv/bin/python devtools/chat.py <workflow_id> --vars '{"customer_id": "c_x", "now": "2026-10-16T15:00"}' "Hello?"

--case opens a fresh practice session for that case and uses its call variables (bank tools then work).
--now overrides the `now` call variable. The chat is ended at the end unless --keep.
"""
import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit"))
sys.path.insert(0, str(ROOT / "devtools"))
import freya  # noqa: E402
import bank  # noqa: E402
from fetch_call import timeline  # noqa: E402
from redact import redact  # noqa: E402


def chat(workflow_id, variables, messages, keep=False, verbose=True):
    call_id, turns = None, []
    for text in messages:
        body = {"workflowId": workflow_id, "message": text, "customerVariables": variables}
        if call_id:
            body["callId"] = call_id
        t0 = time.time()
        try:
            r = freya.api("POST", "/api/v2/chat/message", body)
        except RuntimeError as e:
            turns.append({"user": text, "error": redact(str(e))[:500], "seconds": round(time.time() - t0, 1)})
            if verbose:
                print(f"USER  {text}\n  !! {redact(str(e))[:300]}")
            break
        call_id = r.get("callId") or call_id
        turns.append({"user": text, "agent": r.get("response"), "seconds": round(time.time() - t0, 1)})
        if verbose:
            print(f"USER  {text}\nAGENT ({turns[-1]['seconds']}s) {r.get('response')}")
    if call_id and not keep:
        try:
            freya.api("POST", "/api/v2/chat/end", {"callId": call_id})
        except RuntimeError as e:
            print("chat/end:", redact(str(e))[:200])
    return call_id, turns


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow_id")
    ap.add_argument("messages", nargs="*")
    ap.add_argument("--case")
    ap.add_argument("--vars")
    ap.add_argument("--now")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--timeline", action="store_true", help="print the event timeline after the chat")
    a = ap.parse_args()
    variables = {}
    if a.case:
        st, sess = bank.session(a.case)
        variables = sess["variables"]
    if a.vars:
        variables.update(json.loads(a.vars))
    if a.now:
        variables["now"] = a.now
    print("variables:", redact(json.dumps(variables)))
    cid, _ = chat(a.workflow_id, variables, a.messages, a.keep)
    print("callId:", cid)
    if a.timeline and cid:
        time.sleep(2)
        print(timeline(freya.get_call(cid), show_vars=True))
