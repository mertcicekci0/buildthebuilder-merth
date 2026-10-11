"""Read a bank tool API (OpenAPI 3) into tool contracts. Deterministic: no model call.

A contract is what the compiler needs to emit a correct Freya `api_call` tool:
url, method, request fields (type, enum, required, description), and the response shape.
Field *roles* (which field is the amount, the date, the queue, ...) are inferred by name;
anything ambiguous is reported, never guessed silently.
"""
import json
import re

TOOL_NAMES = ("verify_identity", "get_account", "create_promise", "add_note",
              "schedule_callback", "transfer_to_agent", "close_call")

# role -> (tool, name patterns in priority order). Patterns are generic words, not values from any brief.
ROLE_PATTERNS = {
    "customer_id":      ("*", [r"^customer_?id$", r"^client_?id$", r"customer", r"^id$"]),
    "verify_field":     ("verify_identity", [r"^field$", r"question", r"^type$", r"kind"]),
    "verify_answer":    ("verify_identity", [r"^answer$", r"value", r"response"]),
    "promise_product":  ("create_promise", [r"^product_?id$", r"product", r"account"]),
    "promise_amount":   ("create_promise", [r"^amount$", r"amount", r"sum", r"value"]),
    "promise_date":     ("create_promise", [r"^date$", r"date", r"day", r"when"]),
    "note_product":     ("add_note", [r"^product_?id$", r"product", r"account"]),
    "note_reason":      ("add_note", [r"^reason$", r"reason", r"code", r"category"]),
    "note_text":        ("add_note", [r"^text$", r"text", r"comment", r"note", r"detail"]),
    "callback_date":    ("schedule_callback", [r"^date$", r"date", r"day"]),
    "callback_time":    ("schedule_callback", [r"^time$", r"time", r"hour"]),
    "callback_datetime": ("schedule_callback", [r"datetime", r"date_?time", r"_at$", r"^at$", r"when", r"slot"]),
    "transfer_queue":   ("transfer_to_agent", [r"^queue$", r"queue", r"team", r"department", r"desk"]),
    "close_outcome":    ("close_call", [r"^outcome$", r"outcome", r"result", r"status"]),
    "close_reason":     ("close_call", [r"^reason$", r"reason", r"sub_?outcome", r"detail"]),
}

PRODUCT_ROLE_PATTERNS = {  # resolved in this order; a name taken by an earlier role is not reused
    "product_id": [r"^product_?id$", r"^id$", r"product"],
    "days_late": [r"days_?late", r"days_?past_?due", r"dpd", r"days_?overdue", r"^days", r"late"],
    "product_type": [r"^type$", r"product_?type", r"kind", r"category"],
    "past_due": [r"past_?due", r"overdue", r"arrears", r"due_?amount", r"amount_?due", r"balance"],
}


class OpenAPIError(ValueError):
    pass


def _resolve(spec, node, depth=0):
    if depth > 20:
        raise OpenAPIError("$ref nesting too deep")
    if isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            raise OpenAPIError(f"external $ref not supported: {ref}")
        target = spec
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return _resolve(spec, target, depth + 1)
    if isinstance(node, dict):
        if "allOf" in node:  # merge object parts
            merged = {"type": "object", "properties": {}, "required": []}
            for part in node["allOf"]:
                p = _resolve(spec, part, depth + 1)
                merged["properties"].update(p.get("properties", {}))
                merged["required"] += p.get("required", [])
            return merged
        return {k: _resolve(spec, v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(spec, x, depth + 1) for x in node]
    return node


def _field(name, schema, required):
    f = {"name": name, "type": schema.get("type") or ("string" if "enum" in schema else "string"),
         "required": name in required, "description": schema.get("description", "")}
    for k in ("enum", "format", "items", "properties"):
        if k in schema:
            f[k] = schema[k]
    return f


def _json_body_schema(op):
    content = (op.get("requestBody") or {}).get("content") or {}
    for ctype, c in content.items():
        if "json" in ctype:
            return c.get("schema") or {}
    return {}


def _response_schema(op):
    resps = op.get("responses") or {}
    for code in ("200", "201", 200, 201, "default"):
        r = resps.get(code)
        if r:
            for ctype, c in (r.get("content") or {}).items():
                if "json" in ctype:
                    return c.get("schema") or {}
    return {}


def load(path_or_dict):
    spec = path_or_dict if isinstance(path_or_dict, dict) else json.load(open(path_or_dict))
    if not str(spec.get("openapi", spec.get("swagger", ""))).startswith(("3", "2")):
        raise OpenAPIError("not an OpenAPI document")
    servers = spec.get("servers") or []
    base = (servers[0].get("url") if servers else "").rstrip("/")
    if not base:
        raise OpenAPIError("no servers[0].url: cannot know where the bank API lives")
    contracts, unknown = {}, []
    for path, item in (spec.get("paths") or {}).items():
        item = _resolve(spec, item)
        for method, op in item.items():
            if method.lower() not in ("post", "put", "get", "patch"):
                continue
            name = op.get("operationId") or path.strip("/").split("/")[-1]
            name = name if name in TOOL_NAMES else path.strip("/").split("/")[-1]
            body = _json_body_schema(op)
            req = body.get("required", [])
            fields = [_field(k, v, req) for k, v in (body.get("properties") or {}).items()]
            resp = _response_schema(op)
            c = {"name": name, "url": base + path, "method": method.upper(), "path": path,
                 "summary": op.get("summary", ""), "description": op.get("description", ""),
                 "fields": fields, "required": list(req), "response": resp.get("properties", {}),
                 "response_description": " ".join(str(r.get("description", "")) for r in (op.get("responses") or {}).values())}
            if name in TOOL_NAMES:
                contracts[name] = c
            else:
                unknown.append(name)
    missing = [t for t in TOOL_NAMES if t not in contracts]
    info = spec.get("info") or {}
    return {"base_url": base, "title": info.get("title", ""), "api_description": info.get("description", ""),
            "tools": contracts, "missing_tools": missing, "unknown_operations": unknown}


def _match(names, patterns):
    for pat in patterns:
        hits = [n for n in names if re.search(pat, n, re.I)]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            exact = [n for n in hits if re.fullmatch(pat.strip("^$"), n, re.I)]
            if len(exact) == 1:
                return exact[0], None
            return None, f"ambiguous {hits}"
    return None, "no match"


def infer_roles(api):
    """Map semantic roles to actual field names. Returns (roles, problems)."""
    roles, problems = {}, []
    tools = api["tools"]
    for role, (tool, pats) in ROLE_PATTERNS.items():
        if tool == "*":
            per_tool = {}
            for t, c in tools.items():
                hit, why = _match([f["name"] for f in c["fields"]], pats)
                if hit:
                    per_tool[t] = hit
            names = set(per_tool.values())
            if len(names) == 1:
                roles[role] = names.pop()
            else:
                problems.append(f"{role}: inconsistent across tools {per_tool}")
            continue
        c = tools.get(tool)
        if not c:
            continue
        names = [f["name"] for f in c["fields"] if f["name"] != roles.get("customer_id")]
        hit, why = _match(names, pats)
        if hit:
            roles[role] = hit
        elif role not in ("note_text", "callback_datetime", "callback_date", "callback_time"):
            problems.append(f"{role} in {tool}: {why} among {names}")
    if "callback_datetime" in roles and ("callback_date" in roles or "callback_time" in roles):
        roles.pop("callback_datetime")
    if not (("callback_date" in roles and "callback_time" in roles) or "callback_datetime" in roles) and "schedule_callback" in tools:
        problems.append("schedule_callback: no date/time fields recognised")
    # product fields from get_account's response array
    prod = product_item_schema(api)
    pnames = list((prod or {}).get("properties", {}))
    roles["product_fields"] = {}
    for prole, pats in PRODUCT_ROLE_PATTERNS.items():
        hit, why = _match([n for n in pnames if n not in roles["product_fields"].values()], pats)
        if hit:
            roles["product_fields"][prole] = hit
        else:
            problems.append(f"product {prole}: {why} among {pnames}")
    roles["products_key"] = products_key(api)
    return roles, problems


def products_key(api):
    resp = api["tools"].get("get_account", {}).get("response", {})
    arrays = [k for k, v in resp.items() if isinstance(v, dict) and v.get("type") == "array"]
    return arrays[0] if len(arrays) == 1 else (arrays and next((a for a in arrays if "product" in a.lower()), arrays[0]))


def product_item_schema(api):
    key = products_key(api)
    if not key:
        return None
    return api["tools"]["get_account"]["response"][key].get("items")


def field(api, tool, name):
    for f in api["tools"][tool]["fields"]:
        if f["name"] == name:
            return f
    return None


def enum_of(api, tool, name):
    f = field(api, tool, name) if name else None
    return list(f.get("enum", [])) if f else []


def summary(api, roles):
    """Short human-readable digest for traces and for the extraction prompt."""
    lines = [f"base_url: {api['base_url']}"]
    for t in TOOL_NAMES:
        c = api["tools"].get(t)
        if not c:
            lines.append(f"{t}: MISSING")
            continue
        fs = ", ".join(f"{f['name']}{'*' if f['required'] else ''}:{f['type']}" + (f" in {f['enum']}" if f.get("enum") else "") for f in c["fields"])
        lines.append(f"{t} {c['method']} {c['path']}: {fs}")
    lines.append(f"product fields: {roles.get('product_fields')}")
    return "\n".join(lines)
