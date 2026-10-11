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
REQUIRED_TOOLS = ("verify_identity", "get_account", "create_promise", "close_call")
OPTIONAL_TOOLS = ("add_note", "schedule_callback", "transfer_to_agent")   # a brief that never notes, books or transfers may omit them

# role -> (tool, name patterns in priority order). Patterns are generic words, not values from any brief.
ROLE_PATTERNS = {
    "customer_id":      ("*", [r"^customer_?id$", r"^client_?id$", r"customer", r"^id$"]),
    "verify_field":     ("verify_identity", [r"^field$", r"question", r"^type$", r"kind"]),
    "verify_answer":    ("verify_identity", [r"^answer$", r"value", r"response", r"answer", r"reply"]),
    "promise_product":  ("create_promise", [r"^product_?id$", r"product", r"account", r"_id$", r"_ref$", r"^ref"]),
    "promise_amount":   ("create_promise", [r"^amount$", r"amount", r"sum", r"value"]),
    "promise_date":     ("create_promise", [r"^date$", r"date", r"day", r"when"]),
    "note_product":     ("add_note", [r"^product_?id$", r"product", r"account", r"_id$", r"_ref$", r"^ref"]),
    "note_reason":      ("add_note", [r"^reason$", r"reason", r"code", r"category"]),
    "note_text":        ("add_note", [r"^text$", r"text", r"comment", r"note", r"detail"]),
    "callback_date":    ("schedule_callback", [r"^date$", r"date", r"day"]),
    "callback_time":    ("schedule_callback", [r"^time$", r"time", r"hour"]),
    "callback_datetime": ("schedule_callback", [r"datetime", r"date_?time", r"_at$", r"^at$", r"when", r"slot"]),
    "transfer_queue":   ("transfer_to_agent", [r"^queue$", r"queue", r"team", r"department", r"desk"]),
    "close_outcome":    ("close_call", [r"^outcome$", r"outcome", r"disposition", r"resolution", r"result", r"status"]),
    "close_reason":     ("close_call", [r"^reason$", r"reason", r"sub_?outcome", r"detail"]),
}

PRODUCT_ROLE_PATTERNS = {  # resolved in this order; a name taken by an earlier role is not reused
    "product_id": [r"^product_?id$", r"^id$", r"_id$", r"_ref$", r"^ref", r"product"],
    "days_late": [r"days_?late", r"days_?past_?due", r"dpd", r"days_?overdue", r"^days", r"late",
                  r"overdue_?days", r"arrears_?days", r"days"],
    "product_type": [r"^type$", r"product_?type", r"^product$", r"kind", r"category", r"_type$", r"type"],
    "past_due": [r"past_?due", r"overdue", r"arrears", r"due_?amount", r"amount_?due", r"balance",
                 r"outstanding", r"total_?due", r"owed", r"debt"],
}
# Later patterns are only tried when every earlier one matched nothing, so appending never changes a match that worked.

# Response fields the generated hooks read, by tool. Resolved in order; a name taken by an earlier role is not reused.
# kind: the JSON types a field must have to qualify (when the schema says).
RESPONSE_ROLE_PATTERNS = {
    "verify_identity": [
        ("ok", ("boolean",), [r"^ok$", r"^success$", r"^succeeded$", r"^processed$"]),
        ("error", ("string",), [r"^error$", r"^error_?message$", r"error"]),
        ("all_verified", ("boolean",), [r"^all_?verified$", r"^all_", r"complete", r"fully", r"identity_?(confirmed|verified)",
                                        r"customer_?(confirmed|verified)", r"finished", r"done"]),
        ("verified", ("boolean",), [r"^verified$", r"correct", r"passed", r"match", r"valid", r"right", r"accepted", r"verified", r"confirmed"]),
        ("wrong_answers_left", ("integer", "number"), [r"^wrong_?answers_?left$", r"left", r"remaining", r"attempts", r"tries", r"retries"]),
        ("hint", ("string",), [r"^hint$", r"hint", r"warning", r"notice", r"suggest"]),
    ],
    "*": [
        ("ok", ("boolean",), [r"^ok$", r"^success$", r"^succeeded$", r"success", r"accepted", r"recorded", r"created", r"booked",
                              r"scheduled", r"transferred", r"saved", r"completed", r"^done$"]),
        ("error", ("string",), [r"^error$", r"^error_?message$", r"error"]),
    ],
}
OUTCOME_WORDS = {"promise", "paid", "no_promise", "transferred", "not_verified", "not_reached"}
ID_LIKE = r"(^|_)id$|_ref$|^ref|_no$|_number$|^number$|_key$"
AMOUNT_LIKE = r"amount|due|arrears|balance|overdue|outstanding|owed|debt"
DAYS_LIKE = r"days|dpd|late"
PRODUCT_ARRAY_NAME = r"product|account|agreement|facilit|loan|contract|credit|debt|item"


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


def load(path_or_dict, tool_map=None, base_url=None):
    """tool_map: {canonical tool name: operationId or "METHOD /path"} for contracts that name operations differently (explicit,
    traced; never guessed). base_url: replaces servers[0].url (test builds against our own mock only)."""
    spec = path_or_dict if isinstance(path_or_dict, dict) else json.load(open(path_or_dict))
    if not str(spec.get("openapi", spec.get("swagger", ""))).startswith(("3", "2")):
        raise OpenAPIError("not an OpenAPI document")
    servers = spec.get("servers") or []
    base = (base_url or (servers[0].get("url") if servers else "")).rstrip("/")
    if not base:
        raise OpenAPIError("no servers[0].url: cannot know where the bank API lives")
    if not re.match(r"^https?://[^/{}]+", base) or "{" in base:
        raise OpenAPIError(f"servers[0].url {base!r} is not an absolute URL without variables: tool calls need one")
    alias = {}
    for canon, ref in (tool_map or {}).items():
        if canon not in TOOL_NAMES:
            raise OpenAPIError(f"tool map: {canon!r} is not one of {TOOL_NAMES}")
        ref = str(ref).strip()
        m = re.match(r"^([A-Za-z]+)\s+(/.*)$", ref)
        ref = f"{m.group(1).upper()} {m.group(2)}" if m else ref
        if ref in alias:
            raise OpenAPIError(f"tool map: {alias[ref]!r} and {canon!r} both point at {ref!r}")
        alias[ref] = canon
    used = set()
    contracts, unknown = {}, []
    for path, item in (spec.get("paths") or {}).items():
        item = _resolve(spec, item)
        for method, op in item.items():
            if method.lower() not in ("post", "put", "get", "patch"):
                continue
            name = op.get("operationId") or path.strip("/").split("/")[-1]
            name = name if name in TOOL_NAMES else path.strip("/").split("/")[-1]
            ref = next((r for r in (op.get("operationId") or "", f"{method.upper()} {path}") if r in alias), None)
            if ref:
                name = alias[ref]
                used.add(ref)
            elif name in alias.values():
                name = f"{method.upper()} {path}"   # a tool named by the map is never taken over by an automatically named operation
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
    unmatched = [r for r in alias if r not in used]
    if unmatched:
        raise OpenAPIError(f"tool map entries match no operation in the contract: {unmatched}")
    missing = [t for t in REQUIRED_TOOLS if t not in contracts]
    info = spec.get("info") or {}
    return {"base_url": base, "title": info.get("title", ""), "api_description": info.get("description", ""),
            "tools": contracts, "missing_tools": missing, "absent_optional": [t for t in OPTIONAL_TOOLS if t not in contracts],
            "unknown_operations": unknown}


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


def _fallback(role, c, roles, pf):
    """Structure-based guess for a request role whose name matched no pattern. Returns (name or None, how)."""
    tool_roles = {r for r, (t, _) in ROLE_PATTERNS.items() if t == c["name"]}
    taken = {roles[r] for r in tool_roles if r in roles} | {roles.get("customer_id")}
    free = [f for f in c["fields"] if f["name"] not in taken]
    enums = [f for f in free if f.get("enum")]
    texts = [f for f in free if f["type"] == "string" and not f.get("enum") and not f.get("format")]

    def one(xs):
        return xs[0]["name"] if len(xs) == 1 else None

    def prefer(xs, pats):
        for pat in pats:
            hit = [f for f in xs if re.search(pat, f["name"], re.I)]
            if len(hit) == 1:
                return hit[0]["name"]
        return None
    if role in ("verify_field", "note_reason", "transfer_queue"):
        return one(enums) or one([f for f in enums if f["required"]]), "the only enum field"
    if role == "verify_answer":
        return one([f for f in texts if f["required"]]) or one(texts), "the remaining string field"
    if role == "close_outcome":
        cand = [f for f in enums if not re.search(r"reason|detail|sub", f["name"], re.I)] or enums
        vocab = [f for f in cand if {str(v).lower() for v in f["enum"]} & OUTCOME_WORDS]   # the enum that names call outcomes
        return (one(cand) or one(vocab) or prefer(cand, [r"outcome", r"disposition|resolution", r"result", r"status", r"code"])
                or one([f for f in cand if f["required"]]), "the enum field")
    if role == "close_reason":
        rest = [f for f in free if f["type"] == "string" and not (f.get("enum") and {str(v).lower() for v in f["enum"]} & OUTCOME_WORDS)]
        return one([f for f in rest if not f.get("enum")]) or one(rest), "the other string field"
    if role in ("promise_product", "note_product"):
        if pf.get("product_id") in [f["name"] for f in free]:
            return pf["product_id"], "same name as get_account's product id"
        return one([f for f in free if re.search(ID_LIKE, f["name"], re.I)]), "the id-like field"
    if role == "promise_amount":
        return one([f for f in free if f["type"] in ("number", "integer")]), "the number field"
    if role in ("promise_date", "callback_date"):
        return one([f for f in free if f.get("format") == "date"]), "the date field"
    if role == "callback_datetime":
        return one([f for f in free if f.get("format") == "date-time"]), "the date-time field"
    if role == "callback_time":
        desc = lambda f: (f.get("description") or "").upper()  # noqa: E731
        return one([f for f in free if f.get("format") == "time" or ("HH:MM" in desc(f) and "YYYY" not in desc(f) and f.get("format") != "date-time")]), \
            "the HH:MM field"
    return None, ""


def infer_roles(api):
    """Map semantic roles to actual field names: by name pattern first, then by structure (enum, type, format,
    shared names). Returns (roles, problems); every structural guess is listed for the trace in
    roles['guessed']."""
    roles, problems, guessed = {}, [], {}
    tools = api["tools"]
    # product fields from get_account's response array (resolved first: the request roles may use its id name)
    prod = product_item_schema(api)
    props = (prod or {}).get("properties", {})
    pnames = list(props)
    pfields, pproblems = {}, []
    ask = {f["name"] for f in tools.get("get_account", {}).get("fields", [])}
    promise_names = {f["name"] for f in tools.get("create_promise", {}).get("fields", [])}
    for prole, pats in PRODUCT_ROLE_PATTERNS.items():
        rest = [n for n in pnames if n not in pfields.values()]
        hit, why = _match(rest, pats)
        if prole == "product_id":   # the id-like item field that create_promise also takes beats a name match (e.g. account_id)
            shared_id = [n for n in rest if n in promise_names and n not in ask and re.search(ID_LIKE, n, re.I)]
            if len(shared_id) == 1 and shared_id[0] != hit:
                if hit:
                    guessed[f"product {prole}"] = f"{shared_id[0]} (shared with create_promise, not {hit})"
                hit = shared_id[0]
        if not hit and prole == "product_id":   # the item field create_promise also takes (and get_account does not: the customer id)
            shared = [n for n in rest if n in promise_names and n not in ask]
            hit = shared[0] if len(shared) == 1 else None
            if hit:
                guessed[f"product {prole}"] = hit
        if not hit and prole == "product_type":
            enum_rest = [n for n in rest if (props.get(n) or {}).get("enum") and not re.search(r"status|state", n, re.I)]
            hit = enum_rest[0] if len(enum_rest) == 1 else None
            if hit:
                guessed[f"product {prole}"] = hit
        if hit:
            pfields[prole] = hit
        else:
            pproblems.append(f"product {prole}: {why} among {pnames}")
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
        if role in ("promise_product", "note_product") and pfields.get("product_id") in names and hit != pfields["product_id"]:
            guessed[role] = f"{pfields['product_id']} (same name as get_account's product id{', not ' + hit if hit else ''})"
            hit = pfields["product_id"]   # the same name as get_account's product id wins over e.g. account_id
        if not hit:
            hit, how = _fallback(role, c, roles, pfields)
            if hit:
                guessed[role] = f"{hit} ({how})"
        if hit:
            roles[role] = hit
        elif role not in ("note_text", "callback_datetime", "callback_date", "callback_time"):
            problems.append(f"{role} in {tool}: {why} among {names}")
    if "callback_datetime" in roles and ("callback_date" in roles or "callback_time" in roles):
        roles.pop("callback_datetime")
    if not (("callback_date" in roles and "callback_time" in roles) or "callback_datetime" in roles) and "schedule_callback" in tools:
        problems.append("schedule_callback: no date/time fields recognised")
    roles["product_fields"] = pfields
    problems += pproblems
    roles["products_key"] = products_key(api)
    roles["responses"] = response_roles(api)
    if guessed:
        roles["guessed"] = guessed
    return roles, problems


def response_roles(api):
    """tool -> {role: response field name} for the fields the generated hooks read. A role absent here means the
    response schema has no such field: the hooks then fall back (success = HTTP 2xx without an explicit failure)."""
    out = {}
    for t, c in api["tools"].items():
        resp = c.get("response") or {}
        types = {k: (v.get("type") if isinstance(v, dict) else None) for k, v in resp.items()}
        got = {}
        for role, kinds, pats in RESPONSE_ROLE_PATTERNS.get(t, RESPONSE_ROLE_PATTERNS["*"]):
            cand = [n for n in resp if n not in got.values() and (types[n] is None or types[n] in kinds)]
            hit, _ = _match(cand, pats)
            if not hit and role in ("verified", "wrong_answers_left"):   # the one remaining field of that type
                rest = [n for n in cand if types[n] in kinds and n not in got.values()]
                hit = rest[0] if len(rest) == 1 else None
            if hit:
                got[role] = hit
        out[t] = got
    return out


def _arrays(props, path=(), depth=0):
    """(path, array schema) for every array in a response schema, also inside nested objects."""
    if depth > 4 or not isinstance(props, dict):
        return
    for k, v in props.items():
        if not isinstance(v, dict):
            continue
        if v.get("type") == "array" or "items" in v:
            yield path + (k,), v
        elif v.get("type") == "object" or "properties" in v:
            yield from _arrays(v.get("properties"), path + (k,), depth + 1)


def _item_score(arr):
    item = arr.get("items") or {}
    names = list((item.get("properties") or {}) if isinstance(item, dict) else {})
    if not names:
        return -1
    has = lambda pat: any(re.search(pat, n, re.I) for n in names)  # noqa: E731
    return has(ID_LIKE) + has(AMOUNT_LIKE) + has(DAYS_LIKE) + any((item["properties"][n] or {}).get("enum") for n in names)


def products_path(api):
    """Path (tuple of keys) from get_account's response data to the products array: the array whose items look most
    like late products (an id, an amount and a days field), nested objects included."""
    resp = api["tools"].get("get_account", {}).get("response", {})
    arrays = list(_arrays(resp))
    if not arrays:
        return None
    if len(arrays) == 1:
        return arrays[0][0]
    ranked = sorted(enumerate(arrays), key=lambda ia: (-_item_score(ia[1][1]), not re.search(PRODUCT_ARRAY_NAME, ia[1][0][-1], re.I),
                                                       len(ia[1][0]), ia[0]))
    return ranked[0][1][0]


def products_key(api):
    """Dotted path of the products array inside get_account's data ('products', or 'account.products' when nested):
    Freya flattens the response to <node>_response.data.<path>.<i>.<field>."""
    path = products_path(api)
    return ".".join(path) if path else None


def product_item_schema(api):
    path = products_path(api)
    if not path:
        return None
    node = {"properties": api["tools"]["get_account"]["response"]}
    for k in path:
        node = (node.get("properties") or {}).get(k) or {}
    return node.get("items")


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
