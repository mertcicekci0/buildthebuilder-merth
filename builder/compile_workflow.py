"""Compile a policy IR + the bank's tool API into a Freya workflow. Deterministic: no model call.

Template (verified platform behaviour in probes/README.md):
  init(code) -> opening -> verify -> load_account(api) -> account(code) -> negotiate -> finalize(code)
            -> [note_next(code) -> write_note(api)]* -> goodbye_*(end_call);   lifecycle close_call at end of call.
  opening -> third_party (callback) ; opening/verify/negotiate -> small state-setting code nodes -> goodbye_*.
Writes the model triggers (create_promise, schedule_callback, transfer_to_agent, verify_identity) pass through
pre-hooks that normalise or BLOCK (raise) with an instruction; notes are staged in state and written only at a
non-transfer wrap-up; close_call's outcome is computed from state by its pre-hook.
"""
import copy
import json
import re
import pathlib

from . import openapi_tools as ot

import ast


def _minify(src):
    """Drop comments, docstrings and blank lines from the embedded runtime (smaller workflow, same code)."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(tree)


RUNTIME = _minify((pathlib.Path(__file__).parent / "runtime_src.py").read_text())
_RT_TREE = ast.parse(RUNTIME)
_RT_DEFS = {}  # top-level name -> source of its definition
_RT_HEADER = []  # imports
for _n in _RT_TREE.body:
    if isinstance(_n, (ast.Import, ast.ImportFrom)):
        _RT_HEADER.append(ast.unparse(_n))
    elif isinstance(_n, (ast.FunctionDef, ast.ClassDef)):
        _RT_DEFS[_n.name] = _n
    elif isinstance(_n, ast.Assign):
        for _t in _n.targets:
            if isinstance(_t, ast.Name):
                _RT_DEFS[_t.id] = _n


def _runtime_for(entry):
    """Only the runtime definitions the entry needs (transitively), in source order."""
    def names(node):
        return {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}
    need, todo = set(), set(names(ast.parse(entry))) | {"json"}
    while todo:
        n = todo.pop()
        if n in need or n not in _RT_DEFS:
            continue
        need.add(n)
        todo |= names(_RT_DEFS[n])
    body = [ast.unparse(d) for d in _RT_TREE.body if d in [_RT_DEFS[x] for x in need]]
    return "\n".join(_RT_HEADER + body)
ACCOUNT_NODE = "load_account"


class CompileError(ValueError):
    pass


# ------------------------------------------------------------------ helpers
def _fill(template, mapping):
    out = template
    for k, v in mapping.items():
        out = out.replace("<" + k + ">", v)
    return out


def _code(cfg, entry):
    text = json.dumps(cfg, sort_keys=True, separators=(",", ":")).replace("{{", "{ {").replace("}}", "} }").replace("}}", "} }")
    src = _runtime_for(entry) + "\nCFG = json.loads(" + repr(text) + ")\n" + entry
    if "{{" in src or "}}" in src:
        raise CompileError("generated code contains a double brace")
    return src


def _always(target):
    return {"target_node": target, "description": "always", "condition": {"operator": "always"}}


def _when(var, value, target, desc):
    return {"target_node": target, "description": desc,
            "condition": {"logic": "or", "conditions": [{"variable": var, "operator": "equals", "operand": value}]}}


def _tool_transition(target, description):
    return {"target_node": target, "description": description, "is_tool_call": True,
            "condition": {"operator": "matches_intent", "description": description}}


def _code_node(name, cfg, entry, transitions):
    return {"name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": transitions[-1]["target_node"],
            "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
            "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                             "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": _code(cfg, entry)},
                                                                             "timeout_ms": {"type": "number", "default": 10000}}}}}],
            "transitions": transitions}


def _end_node(name, function_name, message):
    return {"name": name, "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "transitions": [],
            "terminal": {"type": "function", "function": {"name": function_name, "handler": "__function__:end_call",
                                                          "parameters": {"type": "object", "properties": {
                                                              "message_mode": {"type": "string", "default": "custom"},
                                                              "spoken_message": {"type": "string", "default": message}}}}}}


FILLER_TEXT = "Logged by the phone agent"   # a required free-text field the brief gives nothing for
COUNT_NAME = r"count|number_of|^n_|^num_|attempts|tries"


def _extra_required(c, f, engine, model_fields, warnings):
    """A required request field the template does not fill. A model-called tool lets the model fill it; an engine-run
    tool gets a neutral constant for free text, 0 for a count, and nothing (with a warning) for an enum or anything else."""
    name, where = f["name"], c["name"]
    if not engine:
        if warnings is not None:
            warnings.append(f"{where}.{name} is required and has no role: the model fills it from the API description")
        return "{{" + name + "}}"
    if f.get("enum"):
        if warnings is not None:
            warnings.append(f"{where}.{name} is a required enum field with no role: no value can be chosen, the request will lack it")
        return None
    if f["type"] == "string" and not f.get("format"):
        if warnings is not None:
            warnings.append(f"{where}.{name} is required and has no role: sent as the constant {FILLER_TEXT!r}")
        return FILLER_TEXT
    if f["type"] in ("number", "integer") and re.search(COUNT_NAME, name, re.I):
        if warnings is not None:
            warnings.append(f"{where}.{name} is a required count with no role: sent as 0")
        return 0
    if warnings is not None:
        warnings.append(f"{where}.{name} ({f['type']}{', ' + f['format'] if f.get('format') else ''}) is required and has no role: not sent")
    return None


def _headers(api):
    """Content-Type plus the explicit static headers given for this build (--tool-headers): the engine sends `default` values and
    hides them from the model (kit docs/tools-and-code.md, 'Use default for fixed backend config ... auth headers')."""
    hdr = {"Content-Type": {"type": "string", "default": "application/json"}}
    for k, v in (api.get("static_headers") or {}).items():
        hdr[str(k)] = {"type": "string", "default": str(v)}
    return {"type": "object", "properties": hdr}


def _api_tool(api, tool, body_defaults, description="", max_retries=0, pre=None, post=None, model_fields=(), warnings=None):
    """A Freya api_call tool generated from the contract. body_defaults: field -> default; model_fields are left to the model.
    A tool without model_fields is run by the engine; required fields with no role are filled by _extra_required."""
    c = api["tools"][tool]
    props, required = {}, []
    engine = not model_fields
    for f in c["fields"]:
        name = f["name"]
        leaf = {"type": f["type"] if f["type"] in ("string", "number", "integer", "boolean") else "string"}
        if f.get("description"):
            leaf["description"] = f["description"]
        if f.get("enum"):
            leaf["enum"] = list(f["enum"])
            leaf["description"] = (leaf.get("description", "") + " One of: " + ", ".join(map(str, f["enum"])) + ".").strip()
        if name in body_defaults:
            leaf["default"] = body_defaults[name]
        elif name in model_fields:
            leaf["default"] = "{{" + name + "}}"
        elif f["required"]:
            extra = _extra_required(c, f, engine, model_fields, warnings)
            if extra is None:
                continue
            leaf["default"] = extra
        else:
            continue  # optional field we do not use
        props[name] = leaf
        if f["required"] or name in model_fields:
            required.append(name)
    t = {"type": "function", "config": {"timeoutMs": 12000, "maxRetries": max_retries},
         "function": {"name": tool, "handler": "__function__:api_call", "description": description or c["summary"] or tool,
                      "parameters": {"type": "object", "properties": {
                          "url": {"type": "string", "default": c["url"]},
                          "method": {"type": "string", "default": c["method"]},
                          "headers": _headers(api),
                          "body": {"type": "object", "properties": props, "required": required}}}}}
    if pre:
        t["pre_process_code"] = pre
    if post:
        t["post_process_code"] = post
    return t


# ------------------------------------------------------------------ runtime config
NOTE_KEYS = ("refused", "hardship", "dispute", "paid", "service_error")


def clean_var(name, default):
    """A call-variable name as written by the model ('now', '{{now}}', '`now`') -> identifier, else the default."""
    n = re.sub(r"[{}`<>\s]", "", str(name or ""))
    return n if re.fullmatch(r"[A-Za-z_]\w*", n) else default


def var_names(ir):
    """Placeholder -> call-variable name, for roles (<title>), actual names (<salutation>) and other_call_variables."""
    cv = {k: clean_var(v, "") for k, v in (ir.get("call_variables") or {}).items()}
    out = {v: v for v in cv.values() if v}
    out.update({k: v for k, v in cv.items() if v})
    for x in ir.get("other_call_variables", []):
        x = clean_var(x, "")
        if x:
            out[x] = x
    return out


def semantic_note_reason(value, outcomes):
    """Notes are stored as semantic keys; accept an API/brief value from the IR and map it back."""
    v = str(value or "").strip()
    if v in NOTE_KEYS:
        return v
    for table in (outcomes.get("note_reasons") or {}, outcomes.get("reasons") or {}):
        for k, x in table.items():
            if x and x == v and k in NOTE_KEYS:
                return k
    return "refused"


def runtime_config(ir, api, roles):
    pf = roles["product_fields"]
    item = ot.product_item_schema(api) or {}
    known_fields = set(item.get("properties", {}))
    ptypes = {}
    for p in ir["product_types"]:
        if p.get("floor_field") and known_fields and p["floor_field"] not in known_fields:
            raise CompileError(f"floor field {p['floor_field']} is not in get_account's product schema {sorted(known_fields)}")
        # no floor in the policy: the full past-due amount is the only acceptable amount (never 'floor None', never any amount)
        ptypes[p["api_type"]] = {"spoken": p["spoken"], "floor_field": p.get("floor_field") or pf["past_due"]}
    if not pf.get("product_type"):   # no type field: one product type applies to every product; several cannot be told apart
        if len(ptypes) > 1:
            raise CompileError(f"get_account's products have no recognisable type field, but the policy has {len(ptypes)} product types {sorted(ptypes)}")
        ptypes.update({"*": v for v in list(ptypes.values())[:1]})
    o = ir["outcomes"]
    close_enum = ot.enum_of(api, "close_call", roles["close_outcome"])
    for key in ("promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"):
        if close_enum and o.get(key) and o[key] not in close_enum:
            raise CompileError(f"outcome {key} -> {o[key]} is not allowed by close_call ({close_enum})")
    reasons = dict(o.get("reasons", {}))
    reason_enum = ot.enum_of(api, "close_call", roles.get("close_reason"))
    if o.get("paid") != o.get("no_promise") and not (reason_enum and reasons.get("paid") in reason_enum):
        # a distinct paid outcome carries no reason when the reason is free text; a brief that closes "paid" under
        # no_promise names one, and so does an API whose reason codes include the IR's paid reason
        reasons["paid"] = ""
    notes = dict(ir.get("notes") or {"enabled": False})
    notes["default_reason"] = semantic_note_reason(notes.get("default_reason"), o)
    return {
        "now_var": clean_var((ir.get("call_variables") or {}).get("now"), "now"),
        "var_names": var_names(ir),
        "calendar": ir["calendar"], "bands": ir["bands"], "table_days": 45,
        "products_prefix": f"{ACCOUNT_NODE}_response.data.{roles['products_key']}",
        "fields": {"product_id": pf["product_id"], "product_type": pf.get("product_type", "type"), "past_due": pf["past_due"], "days_late": pf["days_late"]},
        "product_types": ptypes, "callback": ir.get("callback") or {},
        "ladder": ir.get("ladder", []),
        "outcomes": {"promise": o["promise"], "paid": o["paid"], "transferred": o["transferred"], "no_promise": o["no_promise"],
                     "not_verified": o["not_verified"], "not_reached": o["not_reached"], "paid_note": "paid",
                     "reasons": reasons, "note_reasons": o.get("note_reasons", {}),
                     **({"by_reason": {k: v for k, v in o["by_reason"].items() if v}} if any((o.get("by_reason") or {}).values()) else {})},
        "transfer_queues": ir.get("transfer_queues", {}),
        "identity": ir["identity"],
        "notes": notes,
        "texts": {"after_promise": (ir.get("recording") or {}).get("after_promise_sentence", ""), "debt_intro": ir.get("debt_intro", ""), "product_sentence": ir.get("product_sentence", ""),
                  "first_ask_single": ir.get("first_ask_single", ""), "first_ask_multi": ir.get("first_ask_multi", ir.get("first_ask_single", "")),
                  "currency": ir.get("currency_word", "")},
        "roles": {k: v for k, v in roles.items() if isinstance(v, str)},
    }


# ------------------------------------------------------------------ code entries (run inside Freya)
ENTRY_INIT = """
try:
    now = parse_now(vars.get(CFG.get('now_var') or 'now') or vars.get('now'))
    st = get_state(vars)
    put_state(vars, st)
    vars['today_spoken'] = spoken_date(now.date()) if now else 'unknown'
    vars['relative_dates'] = relative_dates_text(now.date(), CFG) if now else ''
    vars['close_outcome'] = CFG['outcomes']['not_reached']
    vars['close_reason'] = ''
    vars['id_state'] = 'start'
    vars['cb_done'] = 'no'
    vars['transfer_done'] = 'no'
    vars['init_ok'] = 'yes' if now else 'bad_now'
except Exception as e:
    vars['init_ok'] = 'error ' + str(e)[:200]
"""


def entry_set(**fields):
    lines = ["try:", "    st = get_state(vars)"]
    for k, v in fields.items():
        lines.append(f"    st[{k!r}] = {v!r}")
    lines += ["    put_state(vars, st)", "except Exception as e:", "    vars['set_error'] = str(e)[:200]", ""]
    return "\n".join(lines)


ENTRY_ACCOUNT = """
def _ladder_text(p, today):
    parts = []
    for step in CFG.get('ladder', []):
        if step.get('bands') and p['band'] not in step['bands']:
            continue
        if int(step.get('attempt', 0)) > p['attempts']:
            continue
        if step.get('amount') == 'floor':
            amt = 'the floor ' + money(p['floor'])
        elif step.get('amount') == 'full':
            amt = 'the full amount ' + money(p['full'])
        else:
            amt = 'the full amount ' + money(p['full']) + ' (or the floor ' + money(p['floor']) + ' if the customer already said the full amount is too much)'
        if step.get('date') == 'deadline':
            when = 'on the deadline, ' + spoken_date(datetime.date.fromisoformat(p['deadline'])) if p['deadline'] else 'on the deadline'
        elif step.get('date') == 'end_of_week':
            when = 'on ' + spoken_date(datetime.date.fromisoformat(p['attempt1_date']))
        elif step.get('date') == 'today':
            when = 'today'
        else:
            when = str(step.get('date'))
        parts.append('attempt ' + str(step.get('attempt')) + ': propose ' + amt + ' ' + when)
    return '; '.join(parts) if parts else 'no persuasion attempt'

try:
    now = parse_now(vars.get(CFG.get('now_var') or 'now') or vars.get('now'))
    today = now.date()
    prods = product_facts(load_products(vars, CFG), today, CFG)
    st = get_state(vars)
    st['verified'] = 'yes'
    st['order'] = [p['product_id'] for p in prods]
    st['products'] = dict((p['product_id'], p) for p in prods)
    put_state(vars, st)
    cur = CFG['texts'].get('currency') or ''
    sentences, lines = [], []
    for p in prods:
        m = dict(spoken=p['spoken'], past_due=money(p['full']) + ((' ' + cur) if cur else ''), days_late=str(p['days_late']),
                 floor=money(p['floor']), product_id=p['product_id'])
        s = CFG['texts'].get('product_sentence') or '<spoken>: <past_due> past due, <days_late> days late.'
        for k, v in m.items():
            s = s.replace('<' + k + '>', v)
        sentences.append(s)
        dl = spoken_date(datetime.date.fromisoformat(p['deadline'])) if p['deadline'] else 'unknown'
        lines.append('- ' + p['product_id'] + ': ' + p['spoken'] + '. Full past-due amount ' + money(p['full']) + '. Floor ' + money(p['floor']) +
                     '. ' + str(p['days_late']) + ' days late, ' + (p['band'] or 'no') + ' band, ' + str(p['attempts']) + ' persuasion attempt(s). Latest allowed date (deadline): ' + dl +
                     '. Persuasion: ' + _ladder_text(p, today) + '.')
    def fill_vars(t):
        for k, name in (CFG.get('var_names') or {}).items():
            if vars.get(name):
                t = t.replace('<' + k + '>', str(vars.get(name)))
        return t
    intro = CFG['texts'].get('debt_intro') or ''
    vars['debt_statement'] = fill_vars((intro + ' ' + ' '.join(sentences)).strip())
    ask = CFG['texts'].get('first_ask_multi') if len(prods) > 1 else CFG['texts'].get('first_ask_single')
    vars['first_ask'] = fill_vars(ask or '')
    vars['facts_text'] = '\\n'.join(lines) if lines else 'No product data could be read.'
    vars['date_table'] = date_table(today, prods, CFG)
    vars['n_products'] = str(len(prods))
    vars['account_ok'] = 'yes' if prods else 'empty'
except Exception as e:
    vars['account_ok'] = 'error ' + str(e)[:200]
    vars['debt_statement'] = ''
    vars['first_ask'] = ''
    vars['facts_text'] = 'No product data could be read.'
    vars['date_table'] = ''
"""

ENTRY_FINALIZE = """
try:
    st = get_state(vars)
    pending = []
    default = (CFG.get('notes') or {}).get('default_reason') or 'refused'
    for pid in st.get('order', []):
        if pid in st.get('promises', {}):
            continue
        if not st.get('notes', {}).get(pid):
            st.setdefault('notes', {})[pid] = default
        pending.append(pid)
    st['notes_queue'] = pending if ((CFG.get('notes') or {}).get('enabled') and not st.get('transfer')) else []
    put_state(vars, st)
    vars['has_promise'] = 'yes' if st.get('promises') else 'no'
    vars['notes_pending'] = 'yes' if st['notes_queue'] else 'no'
except Exception as e:
    vars['notes_pending'] = 'no'
    vars['has_promise'] = 'no'
    vars['finalize_error'] = str(e)[:200]
"""

ENTRY_NOTE_NEXT = """
try:
    st = get_state(vars)
    q = st.get('notes_queue') or []
    if q:
        pid = q.pop(0)
        st['notes_queue'] = q
        st.setdefault('notes_written', []).append(pid)
        reason = st.get('notes', {}).get(pid) or 'refused'
        vars['note_pid'] = pid
        vars['note_reason'] = (CFG['outcomes'].get('note_reasons') or {}).get(reason, reason)
        vars['note_go'] = 'yes'
    else:
        vars['note_go'] = 'no'
    put_state(vars, st)
except Exception as e:
    vars['note_go'] = 'no'
    vars['note_error'] = str(e)[:200]
"""

TOOL_SET_OUTCOME = """
try:
    p = params if isinstance(params, dict) else {}
except NameError:
    p = {}
try:
    st = get_state(vars)
    pid = str(p.get('product_id') or '')
    oc = str(p.get('outcome') or '')
    if pid in st.get('products', {}) and pid not in st.get('promises', {}) and oc:
        vars['oc__' + pid] = oc
    else:
        vars['set_outcome_rejected'] = pid + ':' + oc
except Exception as e:
    vars['set_outcome_error'] = str(e)[:200]
"""


def hook_verify_pre(fmt_by_value, field_role, answer_role, order_by_value=None):
    return _join("""
try:
    b = config.get('body') if isinstance(config.get('body'), dict) else {}
    fmt = FORMATS.get(str(b.get(FIELD_ROLE)), 'text')
    raw = str(b.get(ANSWER_ROLE) or '')
    b[ANSWER_ROLE] = normalize_answer(fmt, raw, ORDERS.get(str(b.get(FIELD_ROLE))) or 'DMY')
    config['body'] = b
    vars['verify_sent'] = str(b.get(FIELD_ROLE)) + '=' + str(b.get(ANSWER_ROLE))
except Exception:
    pass
_f = FORMATS.get(str((config.get('body') or {}).get(FIELD_ROLE)), 'text')
_v = str((config.get('body') or {}).get(ANSWER_ROLE) or '')
_n = _f[6:] if _f.startswith('digits') else ''
if (_n.isdigit() and not (_v.isdigit() and len(_v) == int(_n))) or (_f == 'date' and not re.fullmatch(r'\\d{4}-\\d{2}-\\d{2}', _v)):
    vars['verify_sent'] = ''
    raise Exception('NOT SENT (not counted): the answer is incomplete or unreadable (' + _v + '). ' +
                    ('All ' + _n + ' digits are needed.' if _n.isdigit() else 'A whole date with day, month and year is needed, sent as YYYY-MM-DD.') +
                    ' If the customer is still saying it, let them finish; otherwise ask for the rest in one short sentence.')
""", FORMATS=fmt_by_value, ORDERS=order_by_value or {}, FIELD_ROLE=field_role, ANSWER_ROLE=answer_role)


def hook_verify_post(questions, swapped_value, swapped_instruction, failed_message, names=None):
    return _names("""
try:
    d = result.get('data') if isinstance(result, dict) else None
    d = d if isinstance(d, dict) else {}
    true = lambda x: str(x).lower() == 'true'
    left = d.get(@LEFT@)
    asked = str(vars.get('verify_sent') or '').split('=')[0]
    nxt = ''
    for i, q in enumerate(QUESTIONS):
        if q['api_value'] == asked and i + 1 < len(QUESTIONS):
            nxt = QUESTIONS[i + 1]['ask']
    if true(d.get(@VERIFIED@)) and asked:
        vars['idok__' + asked] = 'yes'
    every = all(str(vars.get('idok__' + q['api_value']) or '') == 'yes' for q in QUESTIONS)
    if true(d.get(@ALL@)) or (d.get(@ALL@) is None and true(d.get(@VERIFIED@)) and every):
        vars['id_state'] = 'all'
        msg = 'Identity verified. The system continues now.'
    elif SWAPPED and str(d.get(@HINT@)) == SWAPPED:
        vars['id_state'] = 'swapped'
        msg = 'Not verified yet. ' + SWAPPED_INSTRUCTION
    elif d.get(@HINT@) and not true(d.get(@VERIFIED@)) and str(left) not in ('0', '0.0'):
        # a hint the brief does not describe (e.g. day and month swapped): the service did not count it; never report it as wrong
        vars['id_state'] = @HINT@
        msg = ('Not verified yet, and not counted as a wrong answer: the service says "' + str(d.get(@HINT@)).replace('_', ' ') +
               '". Ask for the same answer again in a way that removes the doubt (for a date: the month as a word).')
    elif str(left) in ('0', '0.0') or 'over' in str(d.get(@ERROR@, '')):
        vars['id_state'] = 'locked'
        msg = 'Too many wrong answers. Do not state any debt. Say: ' + FAILED_MESSAGE
    elif true(d.get(@VERIFIED@)):
        vars['id_state'] = 'next'
        msg = 'That answer is correct. Now ask the next question: ' + nxt if nxt else 'That answer is correct.'
    elif not true(d.get(@OK@, 'true')):
        vars['id_state'] = 'unreadable'
        msg = 'The answer could not be read (not counted). Ask the same question again, clearly.'
    else:
        vars['id_state'] = 'wrong'
        msg = 'That answer is wrong. ' + str(left) + ' wrong answer(s) left in total. Ask the same question once more.'
    d['what_to_do_next'] = msg
    if isinstance(result, dict):
        result['data'] = d
        result.pop('header', None)
except Exception:
    pass
""", _response_names("verify_identity", names), QUESTIONS=questions, SWAPPED=swapped_value or "", SWAPPED_INSTRUCTION=swapped_instruction or "",
       FAILED_MESSAGE=failed_message or "")


def callback_retry_text(alternative_asks=1):
    """What the agent does after a callback time outside the window, for the brief's number of alternative asks."""
    n = _count(alternative_asks, 1)
    if n == 1:
        return "Ask once for another time; if the next answer is also outside, do not schedule and call transition_to_close_no_callback."
    if n == 0:
        return "Do not ask for another time: do not schedule and call transition_to_close_no_callback."
    return (f"Ask for another time, at most {n} times in total after the first time given; if the answer to the {_ordinal(n)} such ask is also outside, "
            "do not schedule and call transition_to_close_no_callback.")


def hook_callback_pre(roles, alternative_asks=1):
    if roles.get("callback_datetime"):
        get_dt = "raw = str(b.get(R_DT) or '')\nd_iso, t = raw[:10], raw[11:16]\n"
    else:
        get_dt = "d_iso, t = str(b.get(R_D) or '')[:10], str(b.get(R_T) or '')[:5]\n"
    return _join("b = config.get('body') if isinstance(config.get('body'), dict) else {}\n" + get_dt + """now = parse_now(vars.get(CFG.get('now_var') or 'now') or vars.get('now'))
v = callback_check(d_iso, t, now, CFG)
if v != 'ok':
    st = get_state(vars)
    st['callback_refusals'] = int(st.get('callback_refusals', 0)) + 1
    put_state(vars, st)
    cb = CFG.get('callback') or {}
    raise Exception('BLOCKED, nothing was sent: ' + d_iso + ' ' + t + ' is outside the allowed callback window (' + v + '). Allowed: ' +
                    ', '.join(cb.get('days', [])) + ' between ' + cb.get('start', '') + ' and ' + cb.get('end', '') +
                    ', later than now. @@RETRY@@')
""".replace("@@RETRY@@", callback_retry_text(alternative_asks)) + (
        # one date-time field: send the checked value in ISO form (the model may say '2026-10-14 15:00')
        "b[R_DT] = d_iso + 'T' + t + ':00'\nconfig['body'] = b\n" if roles.get("callback_datetime") else ""),
        R_DT=roles.get("callback_datetime", ""), R_D=roles.get("callback_date", ""), R_T=roles.get("callback_time", ""))


HOOK_CALLBACK_POST = """
try:
    d = result.get('data') if isinstance(result, dict) else None
    d = d if isinstance(d, dict) else {}
    if @SUCCESS@:
        st = get_state(vars)
        st['contact'] = 'callback'
        put_state(vars, st)
        vars['cb_done'] = 'yes'
        d['what_to_do_next'] = 'Callback booked. The system closes the call now.'
    else:
        d['what_to_do_next'] = 'Not booked (' + str(d.get(@ERROR@)) + '). Ask for another time.'
    if isinstance(result, dict):
        result['data'] = d
        result.pop('header', None)
except Exception:
    pass
"""


def hook_transfer_pre(roles):
    return _join("""
b = config.get('body') if isinstance(config.get('body'), dict) else {}
q = str(b.get(R_QUEUE) or '')
inv = dict((v, k) for k, v in (CFG.get('transfer_queues') or {}).items())
vars['tr__'] = inv.get(q, q)
""", R_QUEUE=roles["transfer_queue"])


SAVE_PLAN_PRE = """
st = get_state(vars)
b = config.get('body') if isinstance(config.get('body'), dict) else {}
raw = str(b.get('plan') or '')
today = parse_now(vars.get(CFG.get('now_var') or 'now') or vars.get('now')).date()
prods = st.get('products', {})
items, errors = [], []
for part in [x.strip() for x in re.split(r'[;\\n]+', raw) if x.strip()]:
    bits = part.replace(',', ' ').split()
    if len(bits) < 3:
        errors.append('cannot read "' + part + '" (use: product_id amount YYYY-MM-DD)')
        continue
    pid, amt, d_raw = bits[0], bits[1], bits[2]
    if pid not in prods:
        errors.append('unknown product ' + pid)
        continue
    if pid in st.get('promises', {}):
        errors.append(pid + ' already has a recorded promise; it cannot be changed in this call')
        continue
    prod = prods[pid]
    try:
        d = datetime.date.fromisoformat(d_raw[:10])
    except ValueError:
        errors.append(pid + ': date must be YYYY-MM-DD, got ' + d_raw)
        continue
    verdict, alt = decide_date(d, prod, today, CFG)
    if verdict != 'ok':
        why = {'past': 'is in the past', 'not_business_day': 'is not a business day', 'after_deadline': 'is after the latest allowed date'}.get(verdict, verdict)
        errors.append(pid + ': ' + spoken_date(d) + ' ' + why + ((', offer ' + spoken_date(alt)) if alt else ''))
        continue
    av = decide_amount(amt, prod)
    if av != 'ok':
        errors.append(pid + ': amount ' + amt + ' ' + {'below_floor': 'is below the floor ' + money(prod['floor']), 'above_full': 'is more than the past-due amount ' + money(prod['full'])}.get(av, 'is missing'))
        continue
    items.append([pid, num(amt), iso(d)])
if errors or not items:
    raise Exception('BLOCKED, nothing was recorded: ' + ('; '.join(errors) or 'no promise in the plan') + '. Correct it with the customer, read back again, confirm, then call save_promises again.')
planned = [x[0] for x in items]
open_ = []
for pid in st.get('order', []):
    if pid in planned or pid in st.get('promises', {}) or str(vars.get('oc__' + pid) or '') or st.get('notes', {}).get(pid):
        continue
    steps = [x for x in CFG.get('ladder', []) if (not x.get('bands') or prods[pid]['band'] in x['bands'])][:max(0, prods[pid]['attempts'])]
    if int(str(vars.get('att__' + pid) or '0')) > len(steps):
        continue   # persuasion used up
    open_.append(pid)
sig = json.dumps(sorted(items))
if open_ and str(vars.get('partial_plan_warned') or '') != sig:
    vars['partial_plan_warned'] = sig
    raise Exception('BLOCKED, nothing was recorded yet: ' + ', '.join(open_) + ' still open (no promise, no outcome, persuasion not finished). '
                    'Finish them first (their next offer, or set_product_outcome), then read back ALL agreed promises together, get one confirmation '
                    'and call save_promises once. If those products already ended (paid, dispute, hardship), call set_product_outcome for them and then '
                    'save_promises again with the same plan, with no new read-back. If the customer insists on recording only these now, call save_promises '
                    'again with the same plan.')
vars['plan_json'] = json.dumps(items)
vars['plan_ready'] = 'yes'
raise Exception('ACCEPTED: the system records these promises now and tells the customer the result. Do not say anything about recording yourself.')
"""

ENTRY_RECORD_NEXT = """
try:
    plan = json.loads(vars.get('plan_json') or '[]')
    vars['plan_ready'] = 'no'
    if plan:
        pid, amount, date = plan.pop(0)
        vars['plan_json'] = json.dumps(plan)
        vars['cur_pid'], vars['cur_amount'], vars['cur_date'] = pid, amount, date
        vars['rec_go'] = 'yes'
    else:
        vars['rec_go'] = 'no'
        st = get_state(vars)
        ok, bad = [], []
        for pid in st.get('order', []):
            p = st['products'].get(pid, {})
            if pid in st.get('promises', {}):
                amount, date = st['promises'][pid]
                ok.append(money(amount) + ' for your ' + p.get('spoken', pid) + ' on ' + spoken_date(datetime.date.fromisoformat(date)))
            elif str(vars.get('oc__' + pid) or '') == 'service_error' and pid in (vars.get('rec_attempted') or ''):
                bad.append(p.get('spoken', pid))
        text = ''
        if ok:
            after = CFG['texts'].get('after_promise') or ''
            for k, name in (CFG.get('var_names') or {}).items():
                if vars.get(name):
                    after = after.replace('<' + k + '>', str(vars.get(name)))
            text += 'Your promise' + ('s are' if len(ok) > 1 else ' is') + ' recorded: ' + '; '.join(ok) + '. ' + after
        if bad:
            text += ' I am sorry, I could not record the promise for your ' + ' and '.join(bad) + '.'
        vars['record_summary'] = text.strip() or 'I could not record the promise.'
except Exception as e:
    vars['rec_go'] = 'no'
    vars['record_summary'] = 'I could not record the promise.'
    vars['record_error'] = str(e)[:200]
"""

HOOK_RECORD_POST = """
try:
    d = result.get('data') if isinstance(result, dict) else None
    d = d if isinstance(d, dict) else {}
    pid = str(vars.get('cur_pid') or '')
    vars['rec_attempted'] = str(vars.get('rec_attempted') or '') + ' ' + pid
    if @SUCCESS@ and pid:
        vars['pr__' + pid] = json.dumps([num(vars.get('cur_amount')), str(vars.get('cur_date'))])
        vars['oc__' + pid] = ''
    elif pid:
        vars['oc__' + pid] = 'service_error'
except Exception:
    pass
"""


REFUSAL_PRE = """
st = get_state(vars)
b = config.get('body') if isinstance(config.get('body'), dict) else {}
pid = str(b.get('product_id') or '').strip()
kind = str(b.get('refused') or 'offer').strip().lower()
prods = st.get('products', {})
if pid not in prods:
    raise Exception('REFUSAL RESULT: unknown product ' + pid + '. Use one of ' + ', '.join(prods) + '.')

def steps_for(p):
    return [x for x in CFG.get('ladder', []) if (not x.get('bands') or p['band'] in x['bands'])][:max(0, p['attempts'])]

def offer_text(p, step, n, total):
    amt = {'floor': 'the floor ' + money(p['floor']), 'full': 'the full amount ' + money(p['full'])}.get(step.get('amount'),
          'the full amount ' + money(p['full']) + ' (or the floor ' + money(p['floor']) + ' if the customer already said the full amount is too much)')
    when = {'deadline': spoken_date(datetime.date.fromisoformat(p['deadline'])) if p.get('deadline') else 'the deadline',
            'end_of_week': spoken_date(datetime.date.fromisoformat(p['attempt1_date'])), 'today': 'today'}.get(step.get('date'), str(step.get('date')))
    return 'persuasion attempt ' + str(n) + ' of ' + str(total) + ' for ' + p['product_id'] + ' (' + p['spoken'] + '): propose ' + amt + ' on ' + when

p = prods[pid]
steps = steps_for(p)
made = int(str(vars.get('att__' + pid) or '0'))
if made > len(steps):
    raise Exception('REFUSAL RESULT: no persuasion attempt left for ' + pid + ' (' + p['spoken'] + '); this was already recorded. Do not repeat an offer. '
                    'Call set_product_outcome with outcome refused for ' + pid + ' if you have not yet.')
if kind == 'today' and 0 < made <= len(steps):
    # a no to "today" never uses an attempt, and it is recorded once per product
    raise Exception('REFUSAL RESULT: the no to paying today is already recorded for ' + pid + '; nothing changed. If you have NOT yet proposed ' +
                    offer_text(p, steps[made - 1], made, len(steps)) + ', propose it now in one short sentence. '
                    'If the customer just refused that proposal, call record_refusal again for ' + pid + ' with refused=offer.')
if kind != 'today' and made == 0:
    made = 1
nxt = made + 1 if (kind != 'today' or made > 0) else 1
if nxt <= len(steps):
    vars['att__' + pid] = str(nxt)
    raise Exception('REFUSAL RESULT: ' + offer_text(p, steps[nxt - 1], nxt, len(steps)) + '. Say it now in one short sentence. If you already said exactly this offer in your last message, ask instead whether that works for them. Never ask about today again.')
vars['att__' + pid] = str(len(steps) + 1)   # exhausted
order = st.get('order', [])
later = [prods[x] for x in order[order.index(pid) + 1:]] if pid in order else []
hint = ''
if later:
    q = later[0]
    qs = steps_for(q)
    if qs and not str(vars.get('att__' + q['product_id']) or ''):
        vars['att__' + q['product_id']] = '1'
    hint = (' Next product in order: ' + offer_text(q, qs[0], 1, len(qs)) + ' (propose it directly; never ask about today again).') if qs else ''
raise Exception('REFUSAL RESULT: no persuasion attempt left for ' + pid + ' (' + p['spoken'] + '). Do not repeat an offer. Call set_product_outcome with outcome refused for ' + pid + '.' +
                (hint or ' Then read back the agreed promises, if any.'))
"""


FINISH_PRE = """
st = get_state(vars)
open_ = [pid for pid in st.get('order', []) if pid not in st.get('promises', {}) and not str(vars.get('oc__' + pid) or '') and not st.get('notes', {}).get(pid)]
if open_ and str(vars.get('wrap_warned') or '') != 'yes' and not st.get('transfer'):
    vars['wrap_warned'] = 'yes'
    raise Exception('BLOCKED, nothing changed: ' + ', '.join(open_) + ' has no recorded promise and no outcome yet. Continue with it, or call set_product_outcome for it. '
                    'If the conversation is really over, call finish_call again.')
vars['wrap_req'] = 'yes'
raise Exception('FINISHED: the system says goodbye and ends the call now. Say nothing more.')
"""


CHECK_DATE_PRE = """
st = get_state(vars)
b = config.get('body') if isinstance(config.get('body'), dict) else {}
today = parse_now(vars.get(CFG.get('now_var') or 'now') or vars.get('now')).date()
prods = st.get('products', {})
pid = str(b.get('product_id') or '').strip()
chosen = [prods[pid]] if pid in prods else list(prods.values())
words = str(b.get('customer_words') or '')
if re.search(r'\\b(twenty|thirty)[\\s.,!?-]*$', words.strip().lower()) and str(vars.get('tens_warned') or '') != words.strip().lower():
    vars['tens_warned'] = words.strip().lower()
    raise Exception('DATE CHECK RESULT (normal answer, not an error): the customer said "' + words.strip() + '", which stops at a number that may have been '
                    'cut off (twenty-something, thirty-something). Never complete it yourself. If they were accepting a date you offered, read back that '
                    'date; otherwise ask them once to say the day again.')
d, kind = resolve_day_words(words, today, CFG) if words else (None, 'none')
if kind == 'vague':
    d = None
elif kind not in ('today', 'relative', 'next_weekday', 'this_weekday', 'weekday') or d is None:
    try:
        d = datetime.date.fromisoformat(str(b.get('date') or '')[:10])
        kind = 'explicit'
    except ValueError:
        d = d if kind == 'explicit' else None
if d is None:
    msg = ('the customer gave no exact day. Ask once for the exact day. If they still give none, propose the latest date: ' +
           '; '.join(p['product_id'] + ' ' + spoken_date(datetime.date.fromisoformat(p['deadline'])) for p in chosen if p.get('deadline')) + '.')
else:
    parts = []
    for p in chosen:
        v, alt = decide_date(d, p, today, CFG)
        name = p['product_id'] + ' (' + p['spoken'] + ')'
        if v == 'ok':
            parts.append(spoken_date(d) + ' is allowed for ' + name + '.')
        elif v == 'not_business_day':
            parts.append(spoken_date(d) + ' is not a business day; for ' + name + ' offer the next business day, ' + spoken_date(alt) + '.')
        elif v == 'after_deadline':
            parts.append(spoken_date(d) + ' is after the latest date for ' + name + '; say the latest date you can record is ' + spoken_date(alt) + ' and offer it.')
        else:
            parts.append(spoken_date(d) + ' is in the past; ask for a day from today on.')
    msg = ' '.join(parts)
raise Exception('DATE CHECK RESULT (normal answer, not an error): ' + msg)
"""


HOOK_TRANSFER_POST = """
try:
    d = result.get('data') if isinstance(result, dict) else None
    d = d if isinstance(d, dict) else {}
    if @SUCCESS@:
        vars['transfer_done'] = 'yes'
        d['what_to_do_next'] = 'Transfer started. The system says goodbye now.'
    else:
        vars['tr__'] = ''
        d['what_to_do_next'] = 'Transfer failed (' + str(d.get(@ERROR@)) + '). Tell the customer and continue.'
    if isinstance(result, dict):
        result['data'] = d
        result.pop('header', None)
except Exception:
    pass
"""


def hook_close_pre(roles, by_reason=False):
    override = "    out = (CFG['outcomes'].get('by_reason') or {}).get(reason, out)\n" if by_reason else ""
    tmpl = ("""
try:
    st = get_state(vars)
    out, reason = outcome_fold(st, CFG)
@@OVERRIDE@@    reason = (CFG['outcomes'].get('reasons') or {}).get(reason, reason)
    b = config.get('body') if isinstance(config.get('body'), dict) else {}
    b[R_OUT] = out
    if reason:
        b[R_REASON] = reason
    else:
        b.pop(R_REASON, None)
    config['body'] = b
except Exception:
    pass
""" if roles.get("close_reason") else """
try:
    st = get_state(vars)
    out, reason = outcome_fold(st, CFG)
@@OVERRIDE@@    b = config.get('body') if isinstance(config.get('body'), dict) else {}
    b[R_OUT] = out
    config['body'] = b
except Exception:
    pass
""")
    return _join(tmpl.replace("@@OVERRIDE@@", override), R_OUT=roles["close_outcome"], R_REASON=roles.get("close_reason") or "")


def _join(template, **consts):
    """Prefix a hook template with constant definitions (repr literals contain no double braces for our data)."""
    head = "".join(f"{k} = {v!r}\n" for k, v in consts.items())
    return head + template


# Response field names the hooks read when the API does not say otherwise (the kit's names).
RESPONSE_DEFAULTS = {"ok": "ok", "error": "error", "verified": "verified", "wrong_answers_left": "wrong_answers_left",
                     "all_verified": "all_verified", "hint": "hint"}
# Success when the response schema has no success flag: an HTTP 2xx without an error and without an explicit false flag.
SUCCESS_2XX = ("(str(result.get('status_code') or (200 if result.get('status') == 'success' else 0))[:1] == '2' and not d.get(@ERROR@) "
               "and 'false' not in (str(d.get('ok')).lower(), str(d.get('success')).lower()))")


def _response_names(tool, names):
    """Placeholder -> literal for a hook template, from roles['responses'][tool] (absent roles keep the default name;
    an absent success flag becomes the HTTP 2xx test)."""
    ok = RESPONSE_DEFAULTS["ok"] if names is None else names.get("ok")   # None: roles from before response roles existed
    names = names or {}
    got = {k: names.get(k) or v for k, v in RESPONSE_DEFAULTS.items()}
    success = ("str(d.get(" + repr(ok) + ")).lower() == 'true'") if ok else SUCCESS_2XX
    return {"@SUCCESS@": success.replace("@ERROR@", repr(got["error"])), "@OK@": repr(got["ok"]), "@ERROR@": repr(got["error"]),
            "@VERIFIED@": repr(got["verified"]), "@LEFT@": repr(got["wrong_answers_left"]), "@ALL@": repr(got["all_verified"]),
            "@HINT@": repr(got["hint"])}


def _names(template, mapping, **consts):
    """_join after replacing @NAME@ placeholders with the API's response field names (as Python literals)."""
    for k, v in mapping.items():
        template = template.replace(k, v)
    if "@" in template.replace("'@'", ""):
        raise CompileError("unfilled response-name placeholder in a hook")
    return _join(template, **consts)


def response_hook(template, tool, roles):
    return _names(template, _response_names(tool, (roles.get("responses") or {}).get(tool)))


def _hook(cfg, body):
    return _code(cfg, body)


# ------------------------------------------------------------------ prompts
_ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth"}


def _ordinal(n):
    return _ORDINALS.get(n, f"{n}th")


def _times(n):
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _count(value, default, low=0):
    """An IR count as an int (the public default when missing or unreadable), never below `low`."""
    try:
        return max(low, int(value))
    except (TypeError, ValueError):
        return default


def _rules(ir, *stages):
    out = []
    for s in stages:
        out += ir.get("stage_rules", {}).get(s, [])
    return "\n".join("- " + r for r in out)


_STUB_FIELDS = {"add_note": [("customer_id", "string"), ("product_id", "string"), ("reason", "string")],
                "schedule_callback": [("customer_id", "string"), ("date", "string"), ("time", "string")],
                "transfer_to_agent": [("customer_id", "string"), ("queue", "string")]}
_STUB_ROLES = {"note_product": "product_id", "note_reason": "reason", "callback_date": "date", "callback_time": "time", "transfer_queue": "queue"}


def _stub_absent_tools(ir, api, roles, absent):
    """Build as if the optional tools existed, then strip every trace of them (see _strip_absent_tools).
    The contract lint afterwards proves nothing calls an endpoint the API does not have."""
    api = copy.deepcopy(api)
    for t in absent:
        api["tools"][t] = {"name": t, "url": api["base_url"] + "/__absent__/" + t, "method": "POST", "path": "/" + t, "summary": "", "description": "",
                           "fields": [{"name": n, "type": ty, "required": True, "description": ""} for n, ty in _STUB_FIELDS[t]],
                           "required": [n for n, _ in _STUB_FIELDS[t]], "response": {}, "response_description": ""}
    per_tool = {"add_note": ("note_product", "note_reason"), "schedule_callback": ("callback_date", "callback_time"), "transfer_to_agent": ("transfer_queue",)}
    roles = dict(roles)
    for t in absent:
        for r in per_tool[t]:
            roles.setdefault(r, _STUB_ROLES[r])
    ir = copy.deepcopy(ir)
    if "add_note" in absent:
        ir.setdefault("notes", {})["enabled"] = False
    return ir, api, roles


def _strip_absent_tools(wf, absent):
    names = set(absent) | ({"transfer_human"} if "transfer_to_agent" in absent else set())
    if "transfer_to_agent" in absent:
        wf["nodes"].pop("transfer_human", None)
    for n in wf["nodes"].values():
        for key in ("tools", "pre_actions"):
            if n.get(key):
                n[key] = [t for t in n[key] if t.get("function", {}).get("name") not in names]
        n["transitions"] = [tr for tr in n.get("transitions", []) if tr.get("target_node") in wf["nodes"]]
        if isinstance(n.get("task_message"), str):
            lines = [ln for ln in n["task_message"].split("\n") if not any(x in ln for x in names)]
            if n["name"] == "someone_else" and "schedule_callback" in absent:
                lines.append("No callback can be booked in this call. Once the person has answered (or will not say), call transition_to_close_no_callback.")
            n["task_message"] = "\n".join(lines)
    return wf


NEEDED_ROLES = {"customer_id": None, "verify_field": "verify_identity", "verify_answer": "verify_identity",
                "promise_product": "create_promise", "promise_amount": "create_promise", "promise_date": "create_promise",
                "close_outcome": "close_call", "transfer_queue": "transfer_to_agent", "note_product": "add_note", "note_reason": "add_note"}


def missing_roles(api, roles):
    """Roles the compiler cannot build without, as sentences (an absent optional tool needs none of its roles)."""
    out = [f"{r} (tool {t or 'all'})" for r, t in NEEDED_ROLES.items() if not roles.get(r) and (t is None or t in api["tools"])]
    if "schedule_callback" in api["tools"] and not (roles.get("callback_datetime") or (roles.get("callback_date") and roles.get("callback_time"))):
        out.append("callback date and time (tool schedule_callback)")
    out += [f"product {r} (get_account)" for r in ("product_id", "past_due", "days_late") if not (roles.get("product_fields") or {}).get(r)]
    if not roles.get("products_key"):
        out.append("products array (get_account)")
    return out


def _usable_queues(ir, api, roles, warnings):
    """Transfer queues the API accepts. An empty or unknown queue drops that transfer path (with a warning) instead of
    failing the build; returns the IR with those queues set to "" and whether any queue is left."""
    if "transfer_to_agent" not in api["tools"]:
        return ir, True   # the whole transfer path is left out as an absent optional tool
    queues = dict(ir.get("transfer_queues") or {})
    queue_enum = ot.enum_of(api, "transfer_to_agent", roles.get("transfer_queue"))
    changed = False
    for sem, q in list(queues.items()):
        q = str(q or "").strip()
        if not q or (queue_enum and q not in queue_enum):
            if warnings is not None:
                warnings.append(f"transfer queue {sem} = {q!r} is " + ("empty" if not q else f"not in the API queues {queue_enum}") +
                                ": that transfer path is left out")
            changed = changed or queues[sem] != ""
            queues[sem] = ""
    if changed:
        ir = dict(ir, transfer_queues=queues)
    return ir, any(queues.values())


def _drop_human_transfer(wf):
    """No queue for a person: remove the transfer_human step and every instruction that leads to it."""
    wf["nodes"].pop("transfer_human", None)
    for n in wf["nodes"].values():
        n["transitions"] = [tr for tr in n.get("transitions", []) if tr.get("target_node") in wf["nodes"]]
        if isinstance(n.get("task_message"), str):
            n["task_message"] = "\n".join(ln for ln in n["task_message"].split("\n") if "transition_to_transfer_human" not in ln)
    return wf


def compile_workflow(ir, api, roles, warnings=None):
    if api["missing_tools"]:
        raise CompileError(f"tool API lacks {api['missing_tools']}")
    lost = missing_roles(api, roles)
    if lost:
        raise CompileError("the tool API's fields for these roles were not recognised: " + "; ".join(lost))
    absent = [t for t in ot.OPTIONAL_TOOLS if t not in api["tools"]]
    ir, any_queue = _usable_queues(ir, api, roles, warnings)
    if "close_reason" in (roles.get("guessed") or {}) and warnings is not None:
        warnings.append(f"close_call reason field guessed by structure: {roles['guessed']['close_reason']}")
    if absent:
        ir, api, roles = _stub_absent_tools(ir, api, roles, absent)
        if warnings is not None:
            warnings.append(f"tool API has no {', '.join(absent)}: those paths are left out")
    wf = _compile(ir, api, roles, warnings)
    if not any_queue and "transfer_to_agent" not in absent:
        absent = absent + ["transfer_to_agent"]   # no usable queue at all: no transfer tool anywhere
    if not (ir.get("transfer_queues") or {}).get("human"):
        wf = _drop_human_transfer(wf)
    return _strip_absent_tools(wf, absent) if absent else wf


SPEAK_ON_ENTRY = {
    "someone_else": "Your first message in this step must always say something to the person, never nothing: if you already asked when the customer "
                    "can be reached, repeat that question in one short sentence.",
    "": "Your first message in this step must always say something to the person, never nothing: if you already asked your question, "
        "repeat it in one short sentence.",
}


def _compile(ir, api, roles, warnings=None):
    cfg = runtime_config(ir, api, roles)
    tool_warnings = []

    def api_tool(*args, **kw):
        return _api_tool(*args, warnings=tool_warnings, **kw)
    cv = {k: clean_var(v, "") for k, v in (ir.get("call_variables") or {}).items()}
    cv = {k: v for k, v in cv.items() if v}
    cust = "{{" + cv.get("customer_id", "customer_id") + "}}"
    bank = ir.get("bank_name", "the bank")
    multi = bool(ir.get("multi_product", {}).get("enabled"))
    notes_on = bool(ir.get("notes", {}).get("enabled"))
    fs = ir.get("fixed_sentences", {})
    idn = ir["identity"]
    rec = ir.get("recording", {})
    verify_values = ot.enum_of(api, "verify_identity", roles["verify_field"])
    for q in idn["questions"]:
        if verify_values and q["api_value"] not in verify_values:
            raise CompileError(f"identity question {q['api_value']} not allowed by verify_identity ({verify_values})")
    queue_enum = ot.enum_of(api, "transfer_to_agent", roles["transfer_queue"])
    for sem, q in ir.get("transfer_queues", {}).items():
        if queue_enum and q and q not in queue_enum:
            raise CompileError(f"transfer queue {sem} -> {q} not in API enum {queue_enum}")

    others = [clean_var(x, "") for x in ir.get("other_call_variables", [])]
    others = [x for x in others if x]
    name_map = {**{v: "{{" + v + "}}" for v in list(cv.values()) + others}, **{k: "{{" + v + "}}" for k, v in cv.items()}}  # <title> and <salutation> both work
    if "first_name" in cv and "last_name" in cv:
        name_map.setdefault("full_name", "{{" + cv["first_name"] + "}} {{" + cv["last_name"] + "}}")
        name_map.setdefault("name", name_map["full_name"])
    raw_q = re.sub(r"(?<!\{)\{([A-Za-z_]\w*)\}(?!\})", r"<\1>", ir["opening"]["identity_question"])   # {title} -> <title>
    identity_question = _fill(raw_q, name_map)
    for leftover in re.findall(r"<([A-Za-z_]\w*)>", identity_question):
        if leftover in ("customer_id", "first_name", "last_name", "title", "now"):   # a role the brief has no variable for: drop it
            identity_question = identity_question.replace("<" + leftover + ">", "")
        else:
            identity_question = identity_question.replace("<" + leftover + ">", "{{" + leftover + "}}")
        if warnings is not None:
            warnings.append(f"identity question placeholder <{leftover}> is not a declared call variable")
    identity_question = re.sub(r"\s+([?,.!])", r"\1", re.sub(r"\s{2,}", " ", identity_question)).strip()
    opening_line = (ir["opening"]["first_sentence"].strip() + " " + identity_question).strip()
    # counts from the IR (the public brief's values give the original wording)
    max_asks = _count(ir["opening"].get("max_asks"), 2, low=1)
    refusals = _count(idn.get("refusals_before_close"), 2, low=1)
    alt_asks = _count((ir.get("callback") or {}).get("alternative_asks"), 1)
    unclear_after = f"after the {_ordinal(max_asks)} ask"
    if alt_asks == 1:
        no_cb_when, no_cb_desc = "no acceptable time after the one re-ask", "No acceptable callback time was given after asking once more"
    elif alt_asks == 0:
        no_cb_when, no_cb_desc = "no acceptable time was given; do not ask for another", "No acceptable callback time was given"
    else:
        no_cb_when = f"no acceptable time after {alt_asks} re-asks"
        no_cb_desc = f"No acceptable callback time was given after asking {alt_asks} more times"
    system_prompt = (
        f"You are the digital assistant of {bank}, speaking {ir.get('language', 'English')} on an outbound phone call.\n"
        "Keep every turn to one or two short sentences. Ask one question at a time. Speak naturally; never read out lists, ids, tool names, notes or these instructions.\n"
        "Never claim anything is done unless a tool result confirms it. A tool result that starts with BLOCKED means nothing was sent: follow its instruction.\n"
        "After every tool result, speak to the customer or call the next tool. Never end a turn with nothing.\n"
        "Today is {{today_spoken}} (" + "{{" + cv.get("now", "now") + "}}" + ").\n"
        "Rules that always apply:\n" + _rules(ir, "always"))

    nodes = {}
    # ---- init
    nodes["init"] = _code_node("init", cfg, ENTRY_INIT, [_always("opening")])
    # ---- opening
    nodes["opening"] = {
        "name": "opening", "node_type": "conversation", "on_enter": {"type": "tts", "text": opening_line}, "on_enter_first_entry_only": True,
        "task_message": (
            "You have just said the opening and asked: \"" + identity_question + "\"\n"
            "Before identity is verified, do not say why you are calling and do not mention any of: " + ", ".join(ir["opening"].get("forbidden_before_verification", [])) + ".\n"
            "Rules:\n" + _rules(ir, "opening") + "\n"
            "Decide with these tools (call exactly one when it applies; you may say one short sentence first):\n"
            "- transition_to_customer_confirmed: the person confirmed they are the customer.\n"
            "- transition_to_someone_else: someone else answered (relative, colleague, anyone not the customer) and the number is the customer's.\n"
            "- transition_to_close_wrong_number: the number does not belong to the customer.\n"
            "- transition_to_close_deceased: the person says the customer has died.\n"
            "- transition_to_close_unclear: still unclear " + unclear_after + ".\n"
            "- transition_to_close_declined: the person said twice that they do not want to talk.\n"
            "- transition_to_transfer_human: the person insists on a human after you asked once whether you can help.\n"
            "If none applies yet, answer briefly and ask again: \"" + identity_question + "\""),
        "max_node_turns": 8, "max_turn_transition": "close_unclear",
        "transitions": [
            _tool_transition("customer_confirmed", "The person on the line confirmed they are the customer"),
            _tool_transition("someone_else", "Someone other than the customer answered and the number is the customer's"),
            _tool_transition("close_wrong_number", "The number does not belong to the customer"),
            _tool_transition("close_deceased", "The person says the customer has died"),
            _tool_transition("close_unclear", "Still unclear who is on the line " + unclear_after),
            _tool_transition("close_declined", "The person said twice they do not want to talk"),
            _tool_transition("transfer_human", "The person insists on speaking to a human"),
        ]}
    # ---- third party
    cb_fields = {roles["customer_id"]: cust}
    cb_model = [roles["callback_datetime"]] if roles.get("callback_datetime") else [roles["callback_date"], roles["callback_time"]]
    nodes["someone_else"] = {
        "name": "someone_else", "node_type": "conversation", "on_enter": {"type": "llm"},
        "task_message": (
            "Someone other than the customer is on the line. Never verify them, never say why you are calling, never mention any account detail, even if they offer to pay.\n"
            "Rules:\n" + _rules(ir, "third_party") + "\n"
            "Today is {{today_spoken}}. Weekday reference:\n{{relative_dates}}\n"
            "When the person names a day and time when the customer can be reached, call schedule_callback with the date (YYYY-MM-DD) and time (HH:MM, 24-hour).\n"
            "Other tools: transition_to_close_no_callback (" + no_cb_when + "), transition_to_close_wrong_number, transition_to_close_deceased."),
        "tools": [api_tool(api, "schedule_callback", cb_fields, "Book a callback for the customer. CALL it as soon as a day and a time are given.",
                            pre=_hook(cfg, hook_callback_pre(roles, alt_asks)), post=_hook(cfg, response_hook(HOOK_CALLBACK_POST, "schedule_callback", roles)), model_fields=cb_model)],
        "max_node_turns": 12, "max_turn_transition": "close_no_callback",
        "transitions": [_when("cb_done", "yes", "goodbye_plain", "callback booked"),
                        _tool_transition("close_no_callback", no_cb_desc),
                        _tool_transition("close_wrong_number", "The number does not belong to the customer"),
                        _tool_transition("close_deceased", "The customer has died")]}
    # ---- identity
    fmt_by_value = {q["api_value"]: q["format"] for q in idn["questions"]}
    order_by_value = {q["api_value"]: ("MDY" if re.sub(r"[^DMY]", "", str(q.get("numeric_order") or "").upper())[:1] == "M" else "DMY")
                      for q in idn["questions"] if q["format"] == "date"}
    date_note = ""
    if order_by_value:
        date_note = " (dates as YYYY-MM-DD; " + ("month before day" if "MDY" in order_by_value.values() else "day before month") + " when said only as numbers)"
    verify_tool = api_tool(api, "verify_identity", {roles["customer_id"]: cust},
                            "Check ONE security answer. CALL it every time the customer gives an answer, with the question's field and the answer exactly as said" + date_note + ".",
                            pre=_hook(cfg, hook_verify_pre(fmt_by_value, roles["verify_field"], roles["verify_answer"], order_by_value)),
                            post=_hook(cfg, hook_verify_post(idn["questions"], idn.get("swapped_hint_value"), idn.get("swapped_instruction"), idn.get("failed_message"),
                                                          names=(roles.get("responses") or {}).get("verify_identity"))),
                            model_fields=[roles["verify_field"], roles["verify_answer"]])
    expl = idn.get('refusal_explanation', '')
    if refusals == 1:
        refusal_text = (f"If the customer refuses to answer: say \"{expl}\" and call transition_to_close_declined. "
                        "If they ask a question back instead of answering (the message holds no answer; why, which account, what this is about): "
                        "answer in one short sentence without saying why you are calling, then ask the question once more; if they refuse, call transition_to_close_declined. ")
    else:
        refusal_text = (f"If the customer refuses, or asks a question back instead of answering (the message holds no answer; why, which account, what this is about): say once \"{expl}\" "
                        "(or answer their question in one short sentence without saying why you are calling) and stop there: do not repeat the security question in that turn. "
                        "If their next message neither answers nor refuses, ask the question once more. ")
        if refusals == 2:
            refusal_text += "If they refuse again (or ask a second time what the call is about instead of answering), call transition_to_close_declined. "
        else:
            refusal_text += (f"If they refuse again (or ask again what the call is about instead of answering), answer in one short sentence and ask the question once more; "
                             f"when they have refused {refusals} times (the {_ordinal(refusals)} refusal), call transition_to_close_declined. ")
    qlist = "\n".join(f"{i + 1}. field {q['api_value']}: \"{q['ask']}\"" for i, q in enumerate(idn["questions"]))
    nodes["customer_confirmed"] = _code_node("customer_confirmed", cfg, entry_set(contact="customer"), [_always("verify")])
    nodes["verify"] = {
        "name": "verify", "node_type": "conversation",
        "on_enter": {"type": "tts", "text": idn["questions"][0]["ask"]}, "on_enter_first_entry_only": True,
        "task_message": (
            "The customer confirmed their name. Verify identity before anything else. Still do not say why you are calling.\n"
            "Security questions, one at a time, in this order:\n" + qlist + "\n"
            "Rules:\n" + _rules(ir, "identity") + "\n"
            "FIRST, every turn: if the customer's message contains an answer to the security question (a whole date, all the digits asked for), call verify_identity with it at once, "
            "before anything else, even if the same message also contains a question, a complaint, small talk or cut-off words. "
            "Example: \"3 May 1985. Why do you need this?\" -> call verify_identity with that date first; the rules below apply only to messages with no answer. "
            "Example: \"The fifth of June nineteen eighty. This is so\" -> the date is complete (day, month and a four-digit year, in digits or words); the words after it were cut off: call verify_identity with 1980-06-05 now, never ask for it again. "
            "A wrong answer that you do not send is still a wrong answer the customer gave: always send it. "
            "Follow the what_to_do_next in its result.\n"
            "An answer may come in pieces over several messages, or just before a cut-off or a complaint: join everything the customer said since you last asked "
            "(\"3 May\" then \"1985\" = 1985-05-03). If they gave an answer you have not checked yet (also one before a cut-off or a complaint), call verify_identity with it now, before replying.\n"
            "Only the result of verify_identity decides that the check is over; the system then says the closing message and ends the call. "
            "If you think enough wrong answers were given but the system has not ended the check, an answer is still unchecked: call verify_identity with it. Never say the closing message yourself.\n"
            "Only if the answer itself is missing or incomplete (small talk only, a date without the year, fewer digits than asked, a message cut off before the answer) is it not an answer yet, and not a refusal: "
            "reply at once by asking the pending question again in one short, slightly rephrased sentence (for a partial answer, ask only for the missing part, such as the year or the remaining digits); "
            "call no tool and never add the refusal explanation to this re-ask. Never invent or complete digits or a year yourself.\n"
            + refusal_text +
            "Only consecutive non-answers to the same question count: any answer, right or wrong, resets this count, and a question about robots, scams or a human is answered under its own rule and never counts as declining.\n"
            "If they insist on a human after you asked once whether you can help, call transition_to_transfer_human.\n"
            "Never say goodbye or end the call yourself in this step: the call ends only through the system (too many wrong answers) or a transition tool."),
        "tools": [verify_tool], "max_node_turns": 30, "max_turn_transition": "close_declined",
        "transitions": [_when("id_state", "all", ACCOUNT_NODE, "identity verified"),
                        _when("id_state", "locked", "close_failed", "too many wrong answers"),
                        _tool_transition("close_declined", "The customer declined the security questions " + _times(refusals)),
                        _tool_transition("transfer_human", "The customer insists on speaking to a human")]}
    # ---- account
    nodes[ACCOUNT_NODE] = {"name": ACCOUNT_NODE, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "account",
                           "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                           "pre_actions": [api_tool(api, "get_account", {roles["customer_id"]: cust}, max_retries=2)],
                           "response_mapping": [], "transitions": [_always("account")]}
    nodes["account"] = _code_node("account", cfg, ENTRY_ACCOUNT, [_always("negotiate")])
    # ---- negotiation
    cp = api["tools"]["create_promise"]
    save_tool = {"type": "function", "config": {"timeoutMs": 8000, "maxRetries": 0},
                 "function": {"name": "save_promises", "handler": "__function__:api_call",
                              "description": "CALL it once, right after the customer clearly confirmed the read-back of ALL agreed promises. The system then records them.",
                              "parameters": {"type": "object", "properties": {
                                  "url": {"type": "string", "default": cp["url"]}, "method": {"type": "string", "default": cp["method"]},
                                  "headers": _headers(api),
                                  "body": {"type": "object", "properties": {
                                      "plan": {"type": "string", "description": "Every confirmed promise as 'product_id amount YYYY-MM-DD', separated by ';'. Example: p1 120 2026-01-09; p2 75.5 2026-01-12",
                                               "default": "{{plan}}"}}, "required": ["plan"]}}}},
                 "pre_process_code": _hook(cfg, SAVE_PLAN_PRE)}
    outcomes_enum = ["refused", "hardship", "dispute", "paid", "service_error"]
    set_outcome_tool = {"type": "function", "function": {
        "name": "set_product_outcome", "handler": "__function__:code_exec",
        "description": "CALL it when a product ends WITHOUT a promise: refused (attempts used up or flat refusal at the end), hardship (declined support), dispute, paid (already paid), service_error (recording failed). It returns nothing: in the same turn, say what comes next (the next product, the read-back, or a short closing remark).",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "default": _code(cfg, TOOL_SET_OUTCOME)},
            "product_id": {"type": "string", "description": "product id, for example p1"},
            "outcome": {"type": "string", "enum": outcomes_enum, "description": "One of: " + ", ".join(outcomes_enum)}},
            "required": ["product_id", "outcome"]}}}
    tq = ir.get("transfer_queues") or {}
    transfer_uses = [(what + " (queue " + tq[sem] + ")") for sem, what in (("restructuring", "restructuring request"),
                                                                           ("support", "customer accepted the support team after hardship"),
                                                                           ("human", "customer insists on a human")) if tq.get(sem)]
    transfer_tool = api_tool(api, "transfer_to_agent", {roles["customer_id"]: cust},
                              "Hand the call to a person. CALL it for: " + ", ".join(transfer_uses) + ".",
                              pre=_hook(cfg, hook_transfer_pre(roles)), post=_hook(cfg, response_hook(HOOK_TRANSFER_POST, "transfer_to_agent", roles)), model_fields=[roles["transfer_queue"]])
    acct = api["tools"]["get_account"]
    check_tool = {"type": "function", "config": {"timeoutMs": 8000, "maxRetries": 0},
                  "function": {"name": "check_date", "handler": "__function__:api_call",
                               "description": "CALL it every time the customer names or changes a payment day, even vaguely, before you answer about that day. It answers immediately with DATE CHECK RESULT.",
                               "parameters": {"type": "object", "properties": {
                                   "url": {"type": "string", "default": acct["url"]}, "method": {"type": "string", "default": acct["method"]},
                                   "headers": _headers(api),
                                   "body": {"type": "object", "properties": {
                                       "product_id": {"type": "string", "description": "product id, or all", "default": "{{product_id}}"},
                                       "customer_words": {"type": "string", "description": "the customer's exact words for the day", "default": "{{customer_words}}"},
                                       "date": {"type": "string", "description": "your reading of the day as YYYY-MM-DD", "default": "{{date}}"}},
                                       "required": ["product_id", "customer_words", "date"]}}}},
                  "pre_process_code": _hook(cfg, CHECK_DATE_PRE)}
    refusal_tool = {"type": "function", "config": {"timeoutMs": 8000, "maxRetries": 0},
                    "function": {"name": "record_refusal", "handler": "__function__:api_call",
                                 "description": "CALL it every time the customer flatly refuses or gives a complete non-answer for a product (including 'no' to paying today, or refusing an offer); never for a cut-off message or small talk. It tells you the next offer, or that the attempts are used up.",
                                 "parameters": {"type": "object", "properties": {
                                     "url": {"type": "string", "default": acct["url"]}, "method": {"type": "string", "default": acct["method"]},
                                     "headers": _headers(api),
                                     "body": {"type": "object", "properties": {
                                         "product_id": {"type": "string", "description": "the product the customer refused", "default": "{{product_id}}"},
                                         "refused": {"type": "string", "enum": ["today", "offer"], "description": "today = the first question about paying today; offer = an offer you proposed",
                                                     "default": "{{refused}}"}},
                                         "required": ["product_id", "refused"]}}}},
                    "pre_process_code": _hook(cfg, REFUSAL_PRE)}
    finish_tool = {"type": "function", "config": {"timeoutMs": 8000, "maxRetries": 0},
                   "function": {"name": "finish_call", "handler": "__function__:api_call",
                                "description": "CALL it on its own when every product has a recorded promise or an outcome and nothing is left to discuss. The system then says goodbye and ends the call.",
                                "parameters": {"type": "object", "properties": {
                                    "url": {"type": "string", "default": acct["url"]}, "method": {"type": "string", "default": acct["method"]},
                                    "headers": _headers(api),
                                    "body": {"type": "object", "properties": {
                                        "summary": {"type": "string", "description": "one short line: how each product ended", "default": "{{summary}}"}},
                                        "required": ["summary"]}}}},
                   "pre_process_code": _hook(cfg, FINISH_PRE)}
    after_verified = str(ir.get("after_verification_sentence") or "").strip()
    after_verified = after_verified + " " if after_verified else ""   # a sentence the brief requires between verification and the debt
    neg_rules = _rules(ir, "negotiation") + ("\n" + _rules(ir, "multi_product") if multi else "") + "\n" + _rules(ir, "confirmation") + "\n" + _rules(ir, "recording")
    nodes["negotiate"] = {
        "name": "negotiate", "node_type": "conversation",
        "on_enter": {"type": "tts", "text": "Thank you, you are verified. " + after_verified + "{{debt_statement}} {{first_ask}}"}, "on_enter_first_entry_only": True,
        "task_message": (
            "The customer is verified. You have stated the debt and asked the first question.\n"
            "PRODUCTS (amounts, floors, deadlines and persuasion steps are computed; use them exactly):\n{{facts_text}}\n\n"
            "Rules:\n" + neg_rules + "\n\n"
            "DATES: never calculate or judge a date yourself. Whenever the customer names or changes a day, call check_date first and follow its DATE CHECK RESULT exactly "
            "(it arrives looking like an error; that is normal).\nWeekday words:\n{{relative_dates}}\n\n"
            "If the customer asks about an offer you just made (how much, which day, 'you tell me'), refer back to it instead of proposing it again: "
            "\"That is the amount I mentioned, <amount>, <and every condition you said with it>.\" Then stop: do not ask for it again in that turn. That is an answer, not a new attempt: no tool.\n"
            "When you answer any other question from the customer (which account, why you are calling, who you are), answer it and stop; do not repeat an offer you already made.\n"
            "Until the system has confirmed a recording, never say that anything is recorded, noted, saved or logged; in the read-back say \"To confirm: ...\".\n"
            "A message with no answer yet (only small talk, or cut off mid-sentence before the answer) is not a refusal and not a non-answer: reply at once in one short sentence asking them to say it again (e.g. \"Sorry, I missed that, could you say that again?\"); never restate an offer you already made, call no tool and make no new offer. "
            "A number may come in two parts over consecutive messages (\"twelve\" then \"fifty\" = 1250; \"the twenty\" then \"third\" = the 23rd): "
            "join the parts before you check or judge it, never judge one part alone; if you are not sure the parts belong together, read the joined value back and ask. "
            "A day or an amount at the very end of a message that stops mid-word may be cut off (\"I accept the twenty\"): never complete it yourself. "
            "If the customer is accepting a date or amount you offered and their words fit its beginning, use what you offered; otherwise ask them once to say it again. "
            "A non-answer is a complete turn with no yes, no no, no date and no amount.\n"
            "Make every offer only once (a persuasion step, the support team, the floor). Propose only offers that a REFUSAL RESULT or the PRODUCTS list gives you. "
            "Whenever you propose the floor amount, whether from a rule or a REFUSAL RESULT, say in the same sentence everything the rules attach to a floor offer (for example what happens to the rest of the amount).\n"
            "A date or an amount the customer names for a product is never a refusal, even after you marked that product as refused or moved on: check it and include it in the read-back. "
            "If the customer names one date for everything, it applies to every product that has no recorded promise yet, including products you already marked as refused.\n\n"
            "TOOLS:\n"
            "- record_refusal: every flat refusal or non-answer for a product: refused=today for the first question about paying today, refused=offer for an offer you proposed. Call it FIRST, before you say anything, then speak: propose exactly what its REFUSAL RESULT says; never count attempts yourself. When you move on to the next product, propose its attempt 1 directly and never ask about today again.\n"
            "- check_date: every time a day is named or changed (product id or all, the customer's exact words, your YYYY-MM-DD reading).\n"
            "- save_promises: once, after a clear yes to the read-back of all agreed promises. If it returns BLOCKED, follow the instruction. ACCEPTED means the system records them now.\n"
            "- set_product_outcome: for every product that ends without a promise. It returns nothing, so keep talking in the same turn.\n"
            "- transfer_to_agent: restructuring, accepted support, or insisted human; then the system says goodbye.\n"
            "- finish_call: when every product has a recorded promise or an outcome, and you have nothing left to say except goodbye. Call it on its own, after the other tools have returned. Do not say goodbye yourself; the system does."),
        "tools": [check_tool, refusal_tool, save_tool, set_outcome_tool, transfer_tool, finish_tool], "max_node_turns": 60, "max_turn_transition": "wrap_up",
        "transitions": [_when("plan_ready", "yes", "record_next", "confirmed promises to record"),
                        _when("transfer_done", "yes", "goodbye_transfer", "transfer done"),
                        _when("wrap_req", "yes", "wrap_up", "the conversation is finished")]}
    # ---- sequential recording (no model involvement, verified: re-entry re-bakes values; engine retries HTTP 500)
    nodes["record_next"] = _code_node("record_next", cfg, ENTRY_RECORD_NEXT, [_when("rec_go", "yes", "write_promise", "record one promise"), _always("post_record")])
    rec_body = {roles["customer_id"]: cust, roles["promise_product"]: "{{cur_pid}}", roles["promise_amount"]: "{{cur_amount}}", roles["promise_date"]: "{{cur_date}}"}
    rec_tool = api_tool(api, "create_promise", rec_body, max_retries=int(rec.get("retries_after_failure", 1)), post=_hook(cfg, response_hook(HOOK_RECORD_POST, "create_promise", roles)))
    for f in api["tools"]["create_promise"]["fields"]:
        if f["name"] == roles["promise_amount"]:
            rec_tool["function"]["parameters"]["properties"]["body"]["properties"][f["name"]]["type"] = "number"
    nodes["write_promise"] = {"name": "write_promise", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "record_next",
                              "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "pre_actions": [rec_tool],
                              "response_mapping": [], "transitions": [_always("record_next")]}
    nodes["post_record"] = {
        "name": "post_record", "node_type": "conversation", "on_enter": {"type": "tts", "text": "{{record_summary}}"},
        "task_message": (
            "You have just told the customer the recording result (above). Recorded promises cannot be changed in this call; if the customer asks, answer as the rules below say.\n"
            "PRODUCTS:\n{{facts_text}}\n"
            "Rules:\n" + _rules(ir, "confirmation", "recording", "closing") + "\n"
            "If a product still has no promise and no outcome, call set_product_outcome for it. If the customer asks for restructuring, support or a human, use transfer_to_agent.\n"
            "When nothing is left, call finish_call on its own (after the other tools have returned). Do not say goodbye yourself; the system does."),
        "tools": [set_outcome_tool, transfer_tool, finish_tool], "max_node_turns": 10, "max_turn_transition": "wrap_up",
        "transitions": [_when("transfer_done", "yes", "goodbye_transfer", "transfer done"),
                        _when("wrap_req", "yes", "wrap_up", "the conversation is finished")]}
    # ---- wrap-up and notes
    nodes["wrap_up"] = _code_node("wrap_up", cfg, ENTRY_FINALIZE, [
        _when("notes_pending", "yes", "note_next", "notes to write"),
        _when("has_promise", "yes", "goodbye_promise", "a promise was recorded"),
        _always("goodbye_plain")])
    nodes["note_next"] = _code_node("note_next", cfg, ENTRY_NOTE_NEXT, [
        _when("note_go", "yes", "write_note", "write one note"),
        _when("has_promise", "yes", "goodbye_promise", "a promise was recorded"),
        _always("goodbye_plain")])
    if notes_on:
        note_body = {roles["customer_id"]: cust, roles["note_product"]: "{{note_pid}}", roles["note_reason"]: "{{note_reason}}"}
        nodes["write_note"] = {"name": "write_note", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "note_next",
                               "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                               "pre_actions": [api_tool(api, "add_note", note_body, max_retries=1)], "response_mapping": [],
                               "transitions": [_always("note_next")]}
    else:
        nodes["note_next"]["transitions"] = [_when("has_promise", "yes", "goodbye_promise", "a promise was recorded"), _always("goodbye_plain")]
    # ---- state-setting closers
    for name, fields, target in [("close_unclear", {"contact": "unclear"}, "goodbye_plain"),
                                 ("close_wrong_number", {"contact": "wrong_number"}, "goodbye_plain"),
                                 ("close_deceased", {"contact": "deceased"}, "goodbye_deceased"),
                                 ("close_no_callback", {"contact": "no_callback"}, "goodbye_plain"),
                                 ("close_declined", {"verified": "declined"}, "goodbye_plain"),
                                 ("close_failed", {"verified": "failed"}, "goodbye_failed")]:
        nodes[name] = _code_node(name, cfg, entry_set(**fields), [_always(target)])
    human_q = ir["transfer_queues"].get("human")
    nodes["transfer_human"] = {"name": "transfer_human", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "goodbye_transfer",
                               "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                               "pre_actions": [api_tool(api, "transfer_to_agent", {roles["customer_id"]: cust, roles["transfer_queue"]: human_q},
                                                         pre=_hook(cfg, hook_transfer_pre(roles)))],
                               "response_mapping": [], "transitions": [_always("goodbye_transfer")]}
    farewell = fs.get("farewell", "Thank you for your time. Goodbye.")
    after = rec.get("after_promise_sentence") or ""
    nodes["goodbye_promise"] = _end_node("goodbye_promise", "ended_promise", farewell)
    nodes["goodbye_plain"] = _end_node("goodbye_plain", "ended_no_promise", farewell)
    nodes["goodbye_failed"] = _end_node("goodbye_failed", "ended_not_verified", (idn.get("failed_message", "") + " " + farewell).strip())
    nodes["goodbye_deceased"] = _end_node("goodbye_deceased", "ended_deceased", (fs.get("deceased", "") + " Goodbye.").strip())
    nodes["goodbye_transfer"] = _end_node("goodbye_transfer", "ended_transferred", "I am connecting you now. " + farewell)

    close_body = {roles["customer_id"]: cust, roles["close_outcome"]: "{{close_outcome}}"}
    if roles.get("close_reason"):
        close_body[roles["close_reason"]] = "{{close_reason}}"
    close = api_tool(api, "close_call", close_body, pre=_hook(cfg, hook_close_pre(roles, bool(cfg["outcomes"].get("by_reason")))))
    close["config"]["alwaysRunsAt"] = ["end_of_call"]
    vn = var_names(ir)

    def fill_prompt(t):
        for k, name in vn.items():
            t = t.replace("<" + k + ">", "{{" + name + "}}")
        return t
    system_prompt = fill_prompt(system_prompt)
    for n in nodes.values():
        if (n.get("on_enter") or {}).get("type") == "llm" and isinstance(n.get("task_message"), str):
            # the model speaks first in this step: it must never produce an empty turn (practice run: silence, then a hang-up)
            first, _, rest = n["task_message"].partition("\n")
            n["task_message"] = first + "\n" + SPEAK_ON_ENTRY.get(n["name"], SPEAK_ON_ENTRY[""]) + ("\n" + rest if rest else "")
    for n in nodes.values():
        if isinstance(n.get("task_message"), str):
            n["task_message"] = fill_prompt(n["task_message"])
        if isinstance((n.get("on_enter") or {}).get("text"), str):
            n["on_enter"]["text"] = fill_prompt(n["on_enter"]["text"])
    test_vars = {cv[k]: v for k, v in {"customer_id": "c_test", "first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"}.items() if k in cv}
    test_vars.update({x: "Example " + x.replace("_", " ") for x in others if x not in test_vars})   # declared variables only
    if warnings is not None:
        warnings.extend(w for w in dict.fromkeys(tool_warnings) if w not in warnings)
    return {"initial_node": "init", "system_prompt": system_prompt, "nodes": nodes, "global_nodes": {}, "tools": [close], "test_variables": test_vars}
