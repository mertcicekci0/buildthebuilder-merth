import copy, json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder import openapi_tools as ot

KIT = json.load(open(ROOT / "kit/tools/openapi.json"))

def test_kit_api_parses_all_tools_and_roles():
    api = ot.load(KIT)
    assert api["missing_tools"] == [] and api["base_url"].endswith("/v1")
    assert api["tools"]["create_promise"]["url"] == "https://freya-bank-mock.freyavoice.workers.dev/v1/create_promise"
    roles, problems = ot.infer_roles(api)
    assert problems == []
    assert roles["customer_id"] == "customer_id"
    assert (roles["promise_amount"], roles["promise_date"], roles["promise_product"]) == ("amount", "date", "product_id")
    assert (roles["callback_date"], roles["callback_time"]) == ("date", "time")
    assert roles["products_key"] == "products"
    assert roles["product_fields"] == {"product_id": "product_id", "product_type": "type", "past_due": "past_due", "days_late": "days_late"}
    assert ot.enum_of(api, "close_call", roles["close_outcome"])[0] == "promise"
    assert ot.enum_of(api, "verify_identity", roles["verify_field"]) == ["date_of_birth", "phone_last4"]

def mutated():
    s = copy.deepcopy(KIT)
    s["servers"][0]["url"] = "https://bank.example.test/v9/"
    p = s["paths"]
    def props(tool): return p[f"/{tool}"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    cp = props("create_promise"); cp["properties"]["payment_date"] = cp["properties"].pop("date"); cp["properties"]["promised_amount"] = cp["properties"].pop("amount")
    cp["required"] = ["customer_id", "product_id", "promised_amount", "payment_date"]
    vi = props("verify_identity"); vi["properties"]["field"]["enum"] = ["postcode", "birth_year"]
    sc = props("schedule_callback"); sc["properties"] = {"customer_id": {"type": "string"}, "callback_at": {"type": "string", "description": "YYYY-MM-DDTHH:MM"}}; sc["required"] = ["customer_id", "callback_at"]
    cc = props("close_call"); cc["properties"]["outcome"]["enum"] = ["ptp", "paid", "transferred", "no_ptp", "id_failed", "no_contact"]
    # move get_account response into components via $ref and rename fields
    ga = p["/get_account"]["post"]["responses"]["200"]["content"]["application/json"]
    item = ga["schema"]["properties"]["products"]["items"]
    item["properties"]["amount_overdue"] = item["properties"].pop("past_due"); item["properties"]["days_past_due"] = item["properties"].pop("days_late")
    s["components"] = {"schemas": {"Account": ga["schema"]}}
    ga["schema"] = {"$ref": "#/components/schemas/Account"}
    return s

def test_mutated_api_roles_follow_the_file():
    api = ot.load(mutated())
    roles, problems = ot.infer_roles(api)
    assert problems == [], problems
    assert api["tools"]["close_call"]["url"] == "https://bank.example.test/v9/close_call"
    assert (roles["promise_amount"], roles["promise_date"]) == ("promised_amount", "payment_date")
    assert roles.get("callback_datetime") == "callback_at" and "callback_date" not in roles
    assert roles["product_fields"]["past_due"] == "amount_overdue" and roles["product_fields"]["days_late"] == "days_past_due"
    assert ot.enum_of(api, "verify_identity", roles["verify_field"]) == ["postcode", "birth_year"]
    assert "no_contact" in ot.enum_of(api, "close_call", roles["close_outcome"])

def test_missing_server_is_an_error():
    s = copy.deepcopy(KIT); s["servers"] = []
    try:
        ot.load(s); assert False
    except ot.OpenAPIError:
        pass
