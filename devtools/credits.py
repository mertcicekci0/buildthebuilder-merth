"""Read the Freya workspace credit balance (documented: GET /api/v2/workspaces/{workspaceId}/credits). Read-only.

    python devtools/credits.py [label]        # prints and appends one JSON line to results/credits.jsonl

The balance is reported in credits with the platform's creditUsdRate; consumption of a test = difference of two readings.
"""
import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit"))
import freya  # noqa: E402


def read():
    r = freya.api("GET", f"/api/v2/workspaces/{freya.workspace_id()}/credits")
    r = r.get("data", r)
    return r.get("result", r)


if __name__ == "__main__":
    res = read()
    row = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "label": " ".join(sys.argv[1:]),
           "creditsLeft": res.get("creditsLeft"), "creditUsdRate": res.get("creditUsdRate"), "billingUnit": res.get("billingUnit")}
    if isinstance(row["creditsLeft"], (int, float)) and isinstance(row["creditUsdRate"], (int, float)):
        row["usd_left"] = round(row["creditsLeft"] * row["creditUsdRate"], 2)
    with open(ROOT / "results" / "credits.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row))
