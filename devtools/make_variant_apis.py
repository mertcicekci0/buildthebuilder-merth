"""Write mutated copies of the kit's OpenAPI for generalisation tests (tests/variants/api-*.json).

    python devtools/make_variant_apis.py

r1: renamed request/response fields, other enums for identity fields, product types, queues, note reasons and
outcomes, a single datetime field for callbacks, and $ref'd schemas. The endpoints do not exist: build with --no-save.
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


if __name__ == "__main__":
    out = ROOT / "tests/variants/api-r1.json"
    out.write_text(json.dumps(r1(), indent=1))
    print("wrote", out.relative_to(ROOT))
