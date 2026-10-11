"""Write tests/variants/api-r6.json: the kit's OpenAPI mutated for brief R6 (Halberd Motor Finance, car finance).

    python devtools/make_variant_api_r6.py

Same seven paths, customer_id kept. Changes: three identity questions (birth_date, postcode, customer_ref_last3) sent as
`question`/`given_answer`, renamed verify response fields and hint value; get_account returns `agreements` with
agreement_id/agreement_type/registration/arrears/days_in_arrears/monthly_payment/minimum_due; create_promise takes
agreement_id/promised_amount/due_date; add_note takes agreement_id/category/free_text with snake_case categories;
schedule_callback takes preferred_date/preferred_time; transfer_to_agent takes `team`; close_call takes
`disposition`/`reason_code`, both enums, hyphenated values. The endpoints do not exist: build with --no-save.
Standalone on purpose (devtools/make_variant_apis.py is edited separately).
"""
import copy
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
KIT = json.loads((ROOT / "kit/tools/openapi.json").read_text())


def body(spec, tool):
    return spec["paths"][f"/{tool}"]["post"]["requestBody"]["content"]["application/json"]["schema"]


def resp(spec, tool):
    return spec["paths"][f"/{tool}"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]


def rename(props, old, new):
    """Rename a property and keep its position."""
    items = [(new if k == old else k, v) for k, v in props.items()]
    props.clear()
    props.update(items)


def r6():
    s = copy.deepcopy(KIT)
    s["info"]["title"] = "Halberd Motor Finance arrears tools"
    s["info"]["description"] = ("Test double of Halberd's servicing systems for automated arrears calls. Every call is written to "
                                "the ledger. The service does not enforce the arrears call specification.")
    s["servers"] = [{"url": "https://r6.example.test/api"}]

    # verify_identity: three questions, renamed request and response fields, another hint value
    s["paths"]["/verify_identity"]["post"]["summary"] = ("Check one security answer. Up to three wrong answers in total are "
                                                         "allowed; the fourth ends the check.")
    vi = body(s, "verify_identity")
    rename(vi["properties"], "field", "question")
    rename(vi["properties"], "answer", "given_answer")
    vi["properties"]["question"]["enum"] = ["birth_date", "postcode", "customer_ref_last3"]
    vi["properties"]["given_answer"]["description"] = (
        "birth_date: YYYY-MM-DD. postcode: capital letters, no spaces. customer_ref_last3: three digits. "
        "An unreadable value is rejected with ok=false and is not counted as a wrong answer.")
    vi["required"] = ["customer_id", "question", "given_answer"]
    vr = resp(s, "verify_identity")["properties"]
    rename(vr, "verified", "correct")
    rename(vr, "wrong_answers_left", "attempts_left")
    rename(vr, "all_verified", "identity_confirmed")
    vr["hint"]["enum"] = ["date_order_suspect"]

    # get_account: agreements instead of products
    ga_op = s["paths"]["/get_account"]["post"]
    ga_op["summary"] = "The agreements in arrears (one to three). Refused until all three questions are passed."
    ga = resp(s, "get_account")["properties"]
    rename(ga, "products", "agreements")
    ga["agreements"]["items"]["properties"] = {
        "agreement_id": {"type": "string"},
        "agreement_type": {"type": "string", "enum": ["hire_purchase", "pcp", "service_plan"]},
        "registration": {"type": "string", "description": "hire_purchase and pcp only: the vehicle registration, e.g. LK21ABC"},
        "arrears": {"type": "number", "description": "pounds and pence"},
        "days_in_arrears": {"type": "integer"},
        "monthly_payment": {"type": "number", "description": "hire_purchase and pcp only"},
        "minimum_due": {"type": "number", "description": "service_plan only"},
    }

    # create_promise
    s["paths"]["/create_promise"]["post"]["summary"] = "Record a payment promise. One per agreement."
    cp = body(s, "create_promise")
    cp["properties"] = {"customer_id": {"type": "string"},
                        "agreement_id": {"type": "string"},
                        "promised_amount": {"type": "number", "description": "pounds and pence"},
                        "due_date": {"type": "string", "format": "date"}}
    cp["required"] = ["customer_id", "agreement_id", "promised_amount", "due_date"]
    rename(resp(s, "create_promise")["properties"], "promise_id", "arrangement_ref")

    # add_note
    s["paths"]["/add_note"]["post"]["summary"] = "Record why an agreement ended the call without a promise."
    an = body(s, "add_note")
    an["properties"] = {"customer_id": {"type": "string"},
                        "agreement_id": {"type": "string"},
                        "category": {"type": "string",
                                     "enum": ["will_not_pay", "financial_difficulty", "disputes_debt", "says_paid", "tool_failure"]},
                        "free_text": {"type": "string"}}
    an["required"] = ["customer_id", "agreement_id", "category"]

    # schedule_callback
    sc = body(s, "schedule_callback")
    sc["properties"] = {"customer_id": {"type": "string"},
                        "preferred_date": {"type": "string", "format": "date"},
                        "preferred_time": {"type": "string", "description": "HH:MM, local time"}}
    sc["required"] = ["customer_id", "preferred_date", "preferred_time"]

    # transfer_to_agent
    s["paths"]["/transfer_to_agent"]["post"]["summary"] = "Hand the call to a Halberd team."
    tr = body(s, "transfer_to_agent")
    rename(tr["properties"], "queue", "team")
    tr["properties"]["team"]["enum"] = ["payment_solutions", "customer_relations", "wellbeing_team"]
    tr["required"] = ["customer_id", "team"]

    # close_call
    cc = body(s, "close_call")
    cc["properties"] = {
        "customer_id": {"type": "string"},
        "disposition": {"type": "string",
                        "enum": ["promise-to-pay", "reported-paid", "handed-off", "no-promise", "id-not-confirmed", "not-contacted"]},
        "reason_code": {"type": "string",
                        "enum": ["wants-new-terms", "asked-for-person", "wellbeing-referral", "will-not-pay", "financial-difficulty",
                                 "disputes-debt", "system-fault", "checks-failed", "checks-refused", "callback-booked",
                                 "no-suitable-time", "wrong-number", "customer-deceased", "not-confirmed", "paid-before-call"],
                        "description": "Required for every disposition except promise-to-pay. See the disposition table of the specification."},
    }
    cc["required"] = ["customer_id", "disposition"]
    return s


if __name__ == "__main__":
    out = ROOT / "tests/variants/api-r6.json"
    out.write_text(json.dumps(r6(), indent=1))
    print("wrote", out.relative_to(ROOT))
