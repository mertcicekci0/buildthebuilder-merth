"""Contract shapes that compile and lint cleanly but would fail at runtime, or policy values the agent can never send.
Reported as trace warnings and an 'ok with warnings' build status (devtools/final_run.sh flags it); nothing is changed."""
import re

from builder import openapi_tools as ot


def contract_warnings(api, roles, ir):
    w = []
    vr = (roles.get("responses") or {}).get("verify_identity") or {}
    if "verify_identity" in api["tools"] and not (vr.get("verified") or vr.get("all_verified")):
        w.append("verify_identity response: no field recognised as 'verified' or 'all verified' (an envelope such as {data: {...}}?): "
                 "correct answers would be treated as wrong")
    for t, c in api["tools"].items():
        if re.search(r"(?<!\{)\{[^{}]+\}(?!\})", c["url"]):
            w.append(f"{t}: URL {c['url']} still has an unfilled path parameter")
        if c.get("query_params"):
            w.append(f"{t}: query parameters {c['query_params']} are not sent by the generated tools")
        if c.get("method") == "GET" and c.get("fields"):
            w.append(f"{t}: GET with body fields: the body may be ignored by the service")
    amt = ot.field(api, "create_promise", roles.get("promise_amount")) if roles.get("promise_amount") else None
    if amt and (amt.get("type") not in ("number", "integer") or re.search(r"cent|minor|pence|kurus", amt["name"], re.I) or amt.get("type") == "integer"):
        w.append(f"create_promise.{amt['name']}: type {amt.get('type')} / name suggests minor units, but amounts are sent as decimal numbers")
    for role in ("promise_date",):
        f = ot.field(api, "create_promise", roles.get(role)) if roles.get(role) else None
        if f and f.get("format") == "date-time":
            w.append(f"create_promise.{f['name']}: format date-time, but a plain date is sent")
    o = ir.get("outcomes") or {}
    produced = {o.get(k) for k in ("promise", "paid", "transferred", "no_promise", "not_verified", "not_reached")}
    produced |= {v for v in (o.get("by_reason") or {}).values() if v}
    for v in ot.enum_of(api, "close_call", roles.get("close_outcome")):
        if v not in produced:
            w.append(f"close_call outcome {v!r} is allowed by the contract but never produced by the policy")
    reasons = {x for x in (o.get("reasons") or {}).values() if x}
    for v in ot.enum_of(api, "close_call", roles.get("close_reason")):
        if v not in reasons:
            w.append(f"close_call reason {v!r} is allowed by the contract but never produced by the policy")
    return w
