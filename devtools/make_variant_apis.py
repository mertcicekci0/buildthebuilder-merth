"""Write mutated copies of the kit's OpenAPI for generalisation tests (tests/variants/api-*.json).

    python devtools/make_variant_apis.py

r1: renamed request/response fields, other enums for identity fields, product types, queues, note reasons and
outcomes, a single datetime field for callbacks, and $ref'd schemas. The endpoints do not exist: build with --no-save.
r2 (business lending, tests/variants/brief-r2.md): company_number/founded_year identity fields, no date hint, a
`facilities` array with product_type/arrears_amount/days_in_arrears and three floor fields, renamed promise and callback
fields, other queues and note reasons, five close_call outcomes (no "paid" one) and an enum on close_call.reason.
"""
import copy
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
KIT = json.loads((ROOT / "kit/tools/openapi.json").read_text())


def body(spec, tool):
    return spec["paths"][f"/{tool}"]["post"]["requestBody"]["content"]["application/json"]["schema"]


def r1():
    s = copy.deepcopy(KIT)
    s["info"]["title"] = "Kestrel Credit Union arrears tools"
    s["info"]["description"] = "Test double of the union's systems. Every call is written to the ledger. The service does not enforce policy."
    s["servers"] = [{"url": "https://kcu-arrears.example.test/api/v2"}]
    vi = body(s, "verify_identity")
    vi["properties"]["field"]["enum"] = ["year_of_birth", "postcode"]
    vi["properties"]["answer"]["description"] = "year_of_birth: four digits. postcode: capitals, no spaces."
    ga = s["paths"]["/get_account"]["post"]["responses"]["200"]["content"]["application/json"]
    item = ga["schema"]["properties"]["products"]["items"]["properties"]
    item["type"]["enum"] = ["card", "overdraft", "auto_loan"]
    item["balance_overdue"] = item.pop("past_due")
    item["days_overdue"] = item.pop("days_late")
    item["min_due"] = item.pop("minimum_payment")
    item["min_due"]["description"] = "cards and overdrafts"
    item["monthly_payment"] = item.pop("instalment_amount")
    item["monthly_payment"]["description"] = "auto loans only"
    s["components"] = {"schemas": {"Account": ga["schema"]}}
    ga["schema"] = {"$ref": "#/components/schemas/Account"}
    cp = body(s, "create_promise")
    cp["properties"]["pay_date"] = cp["properties"].pop("date")
    cp["required"] = ["customer_id", "product_id", "amount", "pay_date"]
    body(s, "add_note")["properties"]["reason"]["enum"] = ["no_pay", "hardship", "dispute", "already_paid", "tech_fail"]
    sc = body(s, "schedule_callback")
    sc["properties"] = {"customer_id": {"type": "string"}, "callback_at": {"type": "string", "description": "Local date and time, YYYY-MM-DDTHH:MM"}}
    sc["required"] = ["customer_id", "callback_at"]
    body(s, "transfer_to_agent")["properties"]["queue"]["enum"] = ["arrears_specialist", "frontdesk", "welfare"]
    cc = body(s, "close_call")
    cc["properties"]["outcome"]["enum"] = ["PTP", "PAID", "XFER", "NOPTP", "NOID", "NOCONTACT"]
    cc["properties"]["reason"]["description"] = "The reason part of the outcome code (see the code list). Omit for PTP and PAID."
    return s


def r2():
    s = copy.deepcopy(KIT)
    s["info"]["title"] = "Copperleaf Business Finance arrears tools"
    s["info"]["description"] = ("Test double of the lender's servicing systems for director arrears calls. Every call is written "
                                "to the ledger. The service does not enforce the call standard.")
    s["servers"] = [{"url": "https://r2.example.test/api"}]
    vi_op = s["paths"]["/verify_identity"]["post"]
    vi_op["summary"] = "Check one security answer about the company. Two wrong answers in total end the check."
    vi = body(s, "verify_identity")
    vi["properties"]["field"]["enum"] = ["company_number", "founded_year"]
    vi["properties"]["answer"]["description"] = ("company_number: the registration number, capitals, no spaces. "
                                                 "founded_year: four digits. An unreadable value is rejected with ok=false.")
    vi_resp = vi_op["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    vi_resp.pop("hint")
    ga_op = s["paths"]["/get_account"]["post"]
    ga_op["summary"] = "The facility in arrears (exactly one). Refused until both questions are passed."
    ga = ga_op["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    ga["facilities"] = ga.pop("products")
    ga["facilities"]["items"]["properties"] = {
        "product_id": {"type": "string"},
        "product_type": {"type": "string", "enum": ["business_loan", "overdraft", "merchant_advance"]},
        "account_last4": {"type": "string", "description": "overdrafts only: last four digits of the business current account"},
        "arrears_amount": {"type": "number", "description": "pounds"},
        "days_in_arrears": {"type": "integer"},
        "monthly_repayment": {"type": "number", "description": "business loans only"},
        "minimum_due": {"type": "number", "description": "overdrafts only"},
        "weekly_minimum": {"type": "number", "description": "merchant cash advances only"},
    }
    cp = body(s, "create_promise")
    cp["properties"]["payment_date"] = cp["properties"].pop("date")
    cp["required"] = ["customer_id", "product_id", "amount", "payment_date"]
    s["paths"]["/add_note"]["post"]["summary"] = "Record why a facility ended without a promise."
    body(s, "add_note")["properties"]["reason"]["enum"] = ["no_promise", "difficulty", "disputed", "paid_claimed", "system_error"]
    sc = body(s, "schedule_callback")
    sc["properties"] = {"customer_id": {"type": "string"},
                        "callback_date": {"type": "string", "format": "date"},
                        "callback_time": {"type": "string", "description": "HH:MM, local time"}}
    sc["required"] = ["customer_id", "callback_date", "callback_time"]
    body(s, "transfer_to_agent")["properties"]["queue"]["enum"] = ["restructuring_desk", "relationship_team", "business_support"]
    cc = body(s, "close_call")
    cc["properties"]["outcome"]["enum"] = ["arrangement_made", "escalated", "no_arrangement", "security_failed", "no_contact"]
    cc["properties"]["reason"] = {
        "type": "string",
        "enum": ["restructure_request", "human_request", "support_referral", "declined_to_pay", "financial_difficulty",
                 "disputed", "payment_claimed", "system_error", "failed_checks", "declined_checks", "callback_booked",
                 "no_callback_time", "wrong_number", "director_deceased", "unconfirmed"],
        "description": "Required for every outcome except arrangement_made. See the outcome table of the call standard.",
    }
    return s


if __name__ == "__main__":
    for name, make in (("r1", r1), ("r2", r2)):
        out = ROOT / f"tests/variants/api-{name}.json"
        out.write_text(json.dumps(make(), indent=1))
        print("wrote", out.relative_to(ROOT))
