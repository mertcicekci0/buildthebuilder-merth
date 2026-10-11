"""Compare an extracted policy IR with a hand-written reference IR, field by field.

    python devtools/ir_diff.py out/x-a.policy.json tests/fixtures/ir_public_a.json [--set path=json ...]

Exact fields (numbers, enums, mappings, calendars) are compared exactly; sentences are compared after
normalisation and reported as "text" differences, which need a human or model judgement, not a fix by default.
Band names may differ between IRs: bands and ladder steps are matched by day ranges. Exit code 1 if any exact field differs.
"""
import json
import re
import sys


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[\"'“”‘’.,!?:;]", "", str(s))).strip().lower()


def band_key(b):
    return (b["min"], b["max"])


def ladder_view(ir):
    by_name = {b["name"]: band_key(b) for b in ir["bands"]}
    all_bands = sorted(by_name.values())
    return [(s["attempt"], s["amount"], s["date"], tuple(sorted(by_name.get(n, (n,)) for n in s["bands"])) if s["bands"] else tuple(all_bands))
            for s in sorted(ir["ladder"], key=lambda s: s["attempt"])]


def get(d, path):
    for part in path.split("."):
        d = d[int(part)] if isinstance(d, list) else d[part]
    return d


EXACT = [
    "call_variables", "identity.max_wrong_total", "identity.swapped_hint_value", "identity.refusals_before_close",
    "opening.max_asks", "calendar.business_weekdays", "calendar.week_start", "callback.days", "callback.start", "callback.end",
    "callback.after_now", "callback.alternative_asks", "multi_product.enabled", "multi_product.one_confirmation",
    "multi_product.clip_shared_date_to_deadline", "notes.enabled", "notes.skip_on_any_transfer",
    "transfer_queues", "outcomes.promise", "outcomes.paid", "outcomes.transferred", "outcomes.no_promise", "outcomes.not_verified",
    "outcomes.not_reached", "outcomes.reasons", "recording.retries_after_failure",
]
TEXT = ["opening.first_sentence", "opening.identity_question", "recording.after_promise_sentence", "recording.read_back_question",
        "fixed_sentences.deceased", "fixed_sentences.robot", "fixed_sentences.scam", "fixed_sentences.paid", "fixed_sentences.farewell",
        "first_ask_single", "first_ask_multi", "debt_intro", "identity.failed_message"]


def diff(got, ref):
    exact, text = [], []
    multi = got["multi_product"]["enabled"] or ref["multi_product"]["enabled"]
    notes = got["notes"]["enabled"] or ref["notes"]["enabled"]
    dates = any(q["format"] == "date" for q in ref["identity"]["questions"])
    for p in EXACT:
        if (p.startswith("multi_product.") and p != "multi_product.enabled" and not multi) or \
           (p.startswith("notes.") and p != "notes.enabled" and not notes) or (p == "identity.swapped_hint_value" and not dates):
            continue  # not used by the compiler in this configuration
        a, b = get(got, p), get(ref, p)
        if p == "outcomes.reasons" and ref["outcomes"]["paid"] != ref["outcomes"]["no_promise"]:
            # a distinct paid outcome carries no reason; the compiler sends reasons.paid only when paid closes under no_promise
            a, b = {k: v for k, v in a.items() if k != "paid"}, {k: v for k, v in b.items() if k != "paid"}
        if isinstance(a, list) and isinstance(b, list) and p.endswith(("business_weekdays", "days")):
            a, b = sorted(a), sorted(b)
        if a != b:
            exact.append((p, a, b))
    if got["notes"]["enabled"] or ref["notes"]["enabled"]:
        if got["outcomes"]["note_reasons"] != ref["outcomes"]["note_reasons"]:
            exact.append(("outcomes.note_reasons", got["outcomes"]["note_reasons"], ref["outcomes"]["note_reasons"]))
    qa = [(q["api_value"], q["format"], q["numeric_order"] if q["format"] == "date" else "") for q in got["identity"]["questions"]]
    qb = [(q["api_value"], q["format"], q["numeric_order"] if q["format"] == "date" else "") for q in ref["identity"]["questions"]]
    if qa != qb:
        exact.append(("identity.questions", qa, qb))
    pa = sorted((t["api_type"], t["floor_field"]) for t in got["product_types"])
    pb = sorted((t["api_type"], t["floor_field"]) for t in ref["product_types"])
    if pa != pb:
        exact.append(("product_types[api_type, floor_field]", pa, pb))
    sa = {t["api_type"]: t["spoken"] for t in got["product_types"]}
    for t in ref["product_types"]:
        if norm(sa.get(t["api_type"], "")) != norm(t["spoken"]):
            text.append((f"product_types[{t['api_type']}].spoken", sa.get(t["api_type"]), t["spoken"]))
    if sorted(got["calendar"]["holidays"]) != sorted(ref["calendar"]["holidays"]):
        exact.append(("calendar.holidays", sorted(got["calendar"]["holidays"]), sorted(ref["calendar"]["holidays"])))
    ba = sorted((band_key(b), b["deadline_business_days"], b["attempts"]) for b in got["bands"])
    bb = sorted((band_key(b), b["deadline_business_days"], b["attempts"]) for b in ref["bands"])
    if ba != bb:
        exact.append(("bands[(min,max), deadline_bd, attempts]", ba, bb))
    la, lb = ladder_view(got), ladder_view(ref)
    if la != lb:
        exact.append(("ladder[(attempt, amount, date, bands)]", la, lb))
    for p in TEXT:
        if p == "first_ask_multi" and not multi:
            continue
        a, b = get(got, p), get(ref, p)
        if norm(a) != norm(b):
            text.append((p, a, b))
    return exact, text


def apply_sets(ir, sets):
    for s in sets:
        path, val = s.split("=", 1)
        parts = path.split(".")
        d = ir
        for part in parts[:-1]:
            d = d[part]
        d[parts[-1]] = json.loads(val)
    return ir


def main(argv):
    sets = [argv[i + 1] for i, a in enumerate(argv) if a == "--set"]
    files = [a for i, a in enumerate(argv) if a != "--set" and (i == 0 or argv[i - 1] != "--set")]
    got, ref = (json.load(open(f)) for f in files[:2])
    ref = apply_sets(ref, sets)
    exact, text = diff(got, ref)
    for p, a, b in exact:
        print(f"EXACT  {p}\n   got: {json.dumps(a)}\n   ref: {json.dumps(b)}")
    for p, a, b in text:
        print(f"TEXT   {p}\n   got: {a}\n   ref: {b}")
    print(f"{len(exact)} exact difference(s), {len(text)} text difference(s)")
    return 1 if exact else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
