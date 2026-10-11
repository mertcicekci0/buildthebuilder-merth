"""Compile a policy IR + the bank's tool API into a Freya workflow. Deterministic: no model call.

Template (verified platform behaviour in probes/README.md):
  init(code) -> opening -> verify -> load_account(api) -> account(code) -> negotiate -> finalize(code)
            -> [note_next(code) -> write_note(api)]* -> goodbye_*(end_call);   lifecycle close_call at end of call.
  opening -> third_party (callback) ; opening/verify/negotiate -> small state-setting code nodes -> goodbye_*.
Writes the model triggers (create_promise, schedule_callback, transfer_to_agent, verify_identity) pass through
pre-hooks that normalise or BLOCK (raise) with an instruction; notes are staged in state and written only at a
non-transfer wrap-up; close_call's outcome is computed from state by its pre-hook.
"""
import json
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


def _api_tool(api, tool, body_defaults, description="", max_retries=0, pre=None, post=None, model_fields=()):
    """A Freya api_call tool generated from the contract. body_defaults: field -> default; model_fields are left to the model."""
    c = api["tools"][tool]
    props, required = {}, []
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
                          "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                          "body": {"type": "object", "properties": props, "required": required}}}}}
    if pre:
        t["pre_process_code"] = pre
    if post:
        t["post_process_code"] = post
    return t


# ------------------------------------------------------------------ runtime config
def runtime_config(ir, api, roles):
    pf = roles["product_fields"]
    item = ot.product_item_schema(api) or {}
    known_fields = set(item.get("properties", {}))
    ptypes = {}
    for p in ir["product_types"]:
        if p.get("floor_field") and known_fields and p["floor_field"] not in known_fields:
            raise CompileError(f"floor field {p['floor_field']} is not in get_account's product schema {sorted(known_fields)}")
        ptypes[p["api_type"]] = {"spoken": p["spoken"], "floor_field": p.get("floor_field")}
    o = ir["outcomes"]
    close_enum = ot.enum_of(api, "close_call", roles["close_outcome"])
    for key in ("promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"):
        if close_enum and o.get(key) and o[key] not in close_enum:
            raise CompileError(f"outcome {key} -> {o[key]} is not allowed by close_call ({close_enum})")
    reasons = o.get("reasons", {})
    return {
        "now_var": (ir.get("call_variables") or {}).get("now") or "now",
        "calendar": ir["calendar"], "bands": ir["bands"], "table_days": 45,
        "products_prefix": f"{ACCOUNT_NODE}_response.data.{roles['products_key']}",
        "fields": {"product_id": pf["product_id"], "product_type": pf.get("product_type", "type"), "past_due": pf["past_due"], "days_late": pf["days_late"]},
        "product_types": ptypes, "callback": ir.get("callback") or {},
        "ladder": ir.get("ladder", []),
        "outcomes": {"promise": o["promise"], "paid": o["paid"], "transferred": o["transferred"], "no_promise": o["no_promise"],
                     "not_verified": o["not_verified"], "not_reached": o["not_reached"], "paid_note": "paid",
                     "reasons": reasons, "note_reasons": o.get("note_reasons", {})},
        "transfer_queues": ir.get("transfer_queues", {}),
        "identity": ir["identity"],
        "notes": ir.get("notes", {"enabled": False}),
        "texts": {"after_promise": (ir.get("recording") or {}).get("after_promise_sentence", ""), "debt_intro": ir.get("debt_intro", ""), "product_sentence": ir.get("product_sentence", ""),
                  "first_ask_single": ir.get("first_ask_single", ""), "first_ask_multi": ir.get("first_ask_multi", ir.get("first_ask_single", "")),
                  "currency": ir.get("currency_word", "")},
        "roles": {k: v for k, v in roles.items() if isinstance(v, str)},
    }


# ------------------------------------------------------------------ code entries (run inside Freya)
ENTRY_INIT = """
try:
    now = parse_now(vars.get(CFG.get('now_var') or 'now'))
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
    now = parse_now(vars.get(CFG.get('now_var') or 'now'))
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
    intro = CFG['texts'].get('debt_intro') or ''
    vars['debt_statement'] = (intro + ' ' + ' '.join(sentences)).strip()
    ask = CFG['texts'].get('first_ask_multi') if len(prods) > 1 else CFG['texts'].get('first_ask_single')
    vars['first_ask'] = ask or ''
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


def hook_verify_pre(fmt_by_value, field_role, answer_role):
    return _join("""
try:
    b = config.get('body') if isinstance(config.get('body'), dict) else {}
    fmt = FORMATS.get(str(b.get(FIELD_ROLE)), 'text')
    raw = str(b.get(ANSWER_ROLE) or '')
    b[ANSWER_ROLE] = normalize_answer(fmt, raw)
    config['body'] = b
    vars['verify_sent'] = str(b.get(FIELD_ROLE)) + '=' + str(b.get(ANSWER_ROLE))
except Exception:
    pass
""", FORMATS=fmt_by_value, FIELD_ROLE=field_role, ANSWER_ROLE=answer_role)


def hook_verify_post(questions, swapped_value, swapped_instruction, failed_message):
    return _join("""
try:
    d = result.get('data') if isinstance(result, dict) else None
    d = d if isinstance(d, dict) else {}
    true = lambda x: str(x).lower() == 'true'
    left = d.get('wrong_answers_left')
    asked = str(vars.get('verify_sent') or '').split('=')[0]
    nxt = ''
    for i, q in enumerate(QUESTIONS):
        if q['api_value'] == asked and i + 1 < len(QUESTIONS):
            nxt = QUESTIONS[i + 1]['ask']
    if true(d.get('all_verified')):
        vars['id_state'] = 'all'
        msg = 'Identity verified. The system continues now.'
    elif SWAPPED and str(d.get('hint')) == SWAPPED:
        vars['id_state'] = 'swapped'
        msg = 'Not verified yet. ' + SWAPPED_INSTRUCTION
    elif str(left) in ('0', '0.0') or 'over' in str(d.get('error', '')):
        vars['id_state'] = 'locked'
        msg = 'Too many wrong answers. Do not state any debt. Say: ' + FAILED_MESSAGE
    elif true(d.get('verified')):
        vars['id_state'] = 'next'
        msg = 'That answer is correct. Now ask the next question: ' + nxt if nxt else 'That answer is correct.'
    elif not true(d.get('ok', 'true')):
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
""", QUESTIONS=questions, SWAPPED=swapped_value or "", SWAPPED_INSTRUCTION=swapped_instruction or "", FAILED_MESSAGE=failed_message or "")


def hook_callback_pre(roles):
    if roles.get("callback_datetime"):
        get_dt = "raw = str(b.get(R_DT) or '')\nd_iso, t = raw[:10], raw[11:16]\n"
    else:
        get_dt = "d_iso, t = str(b.get(R_D) or '')[:10], str(b.get(R_T) or '')[:5]\n"
    return _join("b = config.get('body') if isinstance(config.get('body'), dict) else {}\n" + get_dt + """now = parse_now(vars.get(CFG.get('now_var') or 'now'))
v = callback_check(d_iso, t, now, CFG)
if v != 'ok':
    st = get_state(vars)
    st['callback_refusals'] = int(st.get('callback_refusals', 0)) + 1
    put_state(vars, st)
    cb = CFG.get('callback') or {}
    raise Exception('BLOCKED, nothing was sent: ' + d_iso + ' ' + t + ' is outside the allowed callback window (' + v + '). Allowed: ' +
                    ', '.join(cb.get('days', [])) + ' between ' + cb.get('start', '') + ' and ' + cb.get('end', '') +
                    ', later than now. Ask once for another time; if the next answer is also outside, do not schedule and call transition_to_close_no_callback.')
""", R_DT=roles.get("callback_datetime", ""), R_D=roles.get("callback_date", ""), R_T=roles.get("callback_time", ""))


HOOK_CALLBACK_POST = """
try:
    d = result.get('data') if isinstance(result, dict) else None
    d = d if isinstance(d, dict) else {}
    if str(d.get('ok')).lower() == 'true':
        st = get_state(vars)
        st['contact'] = 'callback'
        put_state(vars, st)
        vars['cb_done'] = 'yes'
        d['what_to_do_next'] = 'Callback booked. The system closes the call now.'
    else:
        d['what_to_do_next'] = 'Not booked (' + str(d.get('error')) + '). Ask for another time.'
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
today = parse_now(vars.get(CFG.get('now_var') or 'now')).date()
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
            text += 'Your promise' + ('s are' if len(ok) > 1 else ' is') + ' recorded: ' + '; '.join(ok) + '. ' + (CFG['texts'].get('after_promise') or '')
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
    if str(d.get('ok')).lower() == 'true' and pid:
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
if kind != 'today' and made == 0:
    made = 1
nxt = made + 1 if (kind != 'today' or made > 0) else 1
if nxt <= len(steps):
    vars['att__' + pid] = str(nxt)
    raise Exception('REFUSAL RESULT: ' + offer_text(p, steps[nxt - 1], nxt, len(steps)) + '. Propose it now in one short sentence. Never ask about today again.')
vars['att__' + pid] = str(made)
order = st.get('order', [])
later = [prods[x] for x in order[order.index(pid) + 1:]] if pid in order else []
hint = ''
if later:
    q = later[0]
    qs = steps_for(q)
    hint = (' Next product in order: ' + offer_text(q, qs[0], 1, len(qs)) + ' (propose it directly; never ask about today again).') if qs else ''
raise Exception('REFUSAL RESULT: no persuasion attempt left for ' + pid + ' (' + p['spoken'] + '). Do not repeat an offer. Call set_product_outcome with outcome refused for ' + pid + '.' +
                (hint or ' Then read back the agreed promises, if any.'))
"""


CHECK_DATE_PRE = """
st = get_state(vars)
b = config.get('body') if isinstance(config.get('body'), dict) else {}
today = parse_now(vars.get(CFG.get('now_var') or 'now')).date()
prods = st.get('products', {})
pid = str(b.get('product_id') or '').strip()
chosen = [prods[pid]] if pid in prods else list(prods.values())
words = str(b.get('customer_words') or '')
d, kind = resolve_day_words(words, today, CFG) if words else (None, 'none')
if kind not in ('today', 'relative', 'next_weekday', 'this_weekday', 'weekday') or d is None:
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
    if str(d.get('ok')).lower() == 'true':
        vars['transfer_done'] = 'yes'
        d['what_to_do_next'] = 'Transfer started. The system says goodbye now.'
    else:
        vars['tr__'] = ''
        d['what_to_do_next'] = 'Transfer failed (' + str(d.get('error')) + '). Tell the customer and continue.'
    if isinstance(result, dict):
        result['data'] = d
        result.pop('header', None)
except Exception:
    pass
"""


def hook_close_pre(roles):
    return _join("""
try:
    st = get_state(vars)
    out, reason = outcome_fold(st, CFG)
    reason = (CFG['outcomes'].get('reasons') or {}).get(reason, reason)
    b = config.get('body') if isinstance(config.get('body'), dict) else {}
    b[R_OUT] = out
    if reason:
        b[R_REASON] = reason
    else:
        b.pop(R_REASON, None)
    config['body'] = b
except Exception:
    pass
""", R_OUT=roles["close_outcome"], R_REASON=roles["close_reason"])


def _join(template, **consts):
    """Prefix a hook template with constant definitions (repr literals contain no double braces for our data)."""
    head = "".join(f"{k} = {v!r}\n" for k, v in consts.items())
    return head + template


def _hook(cfg, body):
    return _code(cfg, body)


# ------------------------------------------------------------------ prompts
def _rules(ir, *stages):
    out = []
    for s in stages:
        out += ir.get("stage_rules", {}).get(s, [])
    return "\n".join("- " + r for r in out)


def compile_workflow(ir, api, roles):
    if api["missing_tools"]:
        raise CompileError(f"tool API lacks {api['missing_tools']}")
    cfg = runtime_config(ir, api, roles)
    cv = ir.get("call_variables", {})
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
        if queue_enum and q not in queue_enum:
            raise CompileError(f"transfer queue {sem} -> {q} not in API enum {queue_enum}")

    name_map = {**{v: "{{" + v + "}}" for v in cv.values()}, **{k: "{{" + v + "}}" for k, v in cv.items()}}  # <title> and <salutation> both work
    opening_line = (ir["opening"]["first_sentence"].strip() + " " + _fill(ir["opening"]["identity_question"], name_map)).strip()
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
            "You have just said the opening and asked whether you are speaking with " + _fill("<title> <last_name>", name_map) + ".\n"
            "Before identity is verified, do not say why you are calling and do not mention any of: " + ", ".join(ir["opening"].get("forbidden_before_verification", [])) + ".\n"
            "Rules:\n" + _rules(ir, "opening") + "\n"
            "Decide with these tools (call exactly one when it applies; you may say one short sentence first):\n"
            "- transition_to_customer_confirmed: the person confirmed they are the customer.\n"
            "- transition_to_someone_else: someone else answered (relative, colleague, anyone not the customer) and the number is the customer's.\n"
            "- transition_to_close_wrong_number: the number does not belong to the customer.\n"
            "- transition_to_close_deceased: the person says the customer has died.\n"
            "- transition_to_close_unclear: still unclear after the second ask.\n"
            "- transition_to_close_declined: the person said twice that they do not want to talk.\n"
            "- transition_to_transfer_human: the person insists on a human after you asked once whether you can help.\n"
            "If none applies yet, answer briefly and ask again whether you are speaking with " + _fill("<title> <last_name>", name_map) + "."),
        "max_node_turns": 8, "max_turn_transition": "close_unclear",
        "transitions": [
            _tool_transition("customer_confirmed", "The person on the line confirmed they are the customer"),
            _tool_transition("someone_else", "Someone other than the customer answered and the number is the customer's"),
            _tool_transition("close_wrong_number", "The number does not belong to the customer"),
            _tool_transition("close_deceased", "The person says the customer has died"),
            _tool_transition("close_unclear", "Still unclear who is on the line after the second ask"),
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
            "Other tools: transition_to_close_no_callback (no acceptable time after the one re-ask), transition_to_close_wrong_number, transition_to_close_deceased."),
        "tools": [_api_tool(api, "schedule_callback", cb_fields, "Book a callback for the customer. CALL it as soon as a day and a time are given.",
                            pre=_hook(cfg, hook_callback_pre(roles)), post=_hook(cfg, HOOK_CALLBACK_POST), model_fields=cb_model)],
        "max_node_turns": 12, "max_turn_transition": "close_no_callback",
        "transitions": [_when("cb_done", "yes", "goodbye_plain", "callback booked"),
                        _tool_transition("close_no_callback", "No acceptable callback time was given after asking once more"),
                        _tool_transition("close_wrong_number", "The number does not belong to the customer"),
                        _tool_transition("close_deceased", "The customer has died")]}
    # ---- identity
    fmt_by_value = {q["api_value"]: q["format"] for q in idn["questions"]}
    verify_tool = _api_tool(api, "verify_identity", {roles["customer_id"]: cust},
                            "Check ONE security answer. CALL it every time the customer gives an answer, with the question's field and the answer exactly as said (dates as YYYY-MM-DD, day before month when said as numbers).",
                            pre=_hook(cfg, hook_verify_pre(fmt_by_value, roles["verify_field"], roles["verify_answer"])),
                            post=_hook(cfg, hook_verify_post(idn["questions"], idn.get("swapped_hint_value"), idn.get("swapped_instruction"), idn.get("failed_message"))),
                            model_fields=[roles["verify_field"], roles["verify_answer"]])
    qlist = "\n".join(f"{i + 1}. field {q['api_value']}: \"{q['ask']}\"" for i, q in enumerate(idn["questions"]))
    nodes["customer_confirmed"] = _code_node("customer_confirmed", cfg, entry_set(contact="customer"), [_always("verify")])
    nodes["verify"] = {
        "name": "verify", "node_type": "conversation",
        "on_enter": {"type": "tts", "text": idn["questions"][0]["ask"]}, "on_enter_first_entry_only": True,
        "task_message": (
            "The customer confirmed their name. Verify identity before anything else. Still do not say why you are calling.\n"
            "Security questions, one at a time, in this order:\n" + qlist + "\n"
            "Rules:\n" + _rules(ir, "identity") + "\n"
            "Call verify_identity after each answer and follow the what_to_do_next in its result. If an answer comes in pieces, wait until it is complete.\n"
            f"If the customer refuses: say once \"{idn.get('refusal_explanation', '')}\". "
            "If they refuse again (or ask a second time what the call is about instead of answering), call transition_to_close_declined.\n"
            "If they insist on a human after you asked once whether you can help, call transition_to_transfer_human."),
        "tools": [verify_tool], "max_node_turns": 30, "max_turn_transition": "close_declined",
        "transitions": [_when("id_state", "all", ACCOUNT_NODE, "identity verified"),
                        _when("id_state", "locked", "close_failed", "too many wrong answers"),
                        _tool_transition("close_declined", "The customer declined the security questions twice"),
                        _tool_transition("transfer_human", "The customer insists on speaking to a human")]}
    # ---- account
    nodes[ACCOUNT_NODE] = {"name": ACCOUNT_NODE, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "account",
                           "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                           "pre_actions": [_api_tool(api, "get_account", {roles["customer_id"]: cust}, max_retries=2)],
                           "response_mapping": [], "transitions": [_always("account")]}
    nodes["account"] = _code_node("account", cfg, ENTRY_ACCOUNT, [_always("negotiate")])
    # ---- negotiation
    cp = api["tools"]["create_promise"]
    save_tool = {"type": "function", "config": {"timeoutMs": 8000, "maxRetries": 0},
                 "function": {"name": "save_promises", "handler": "__function__:api_call",
                              "description": "CALL it once, right after the customer clearly confirmed the read-back of ALL agreed promises. The system then records them.",
                              "parameters": {"type": "object", "properties": {
                                  "url": {"type": "string", "default": cp["url"]}, "method": {"type": "string", "default": cp["method"]},
                                  "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                                  "body": {"type": "object", "properties": {
                                      "plan": {"type": "string", "description": "Every confirmed promise as 'product_id amount YYYY-MM-DD', separated by ';'. Example: p1 120 2026-01-09; p2 75.5 2026-01-12",
                                               "default": "{{plan}}"}}, "required": ["plan"]}}}},
                 "pre_process_code": _hook(cfg, SAVE_PLAN_PRE)}
    outcomes_enum = ["refused", "hardship", "dispute", "paid", "service_error"]
    set_outcome_tool = {"type": "function", "function": {
        "name": "set_product_outcome", "handler": "__function__:code_exec",
        "description": "CALL it when a product ends WITHOUT a promise: refused (attempts used up or flat refusal at the end), hardship (declined support), dispute, paid (already paid), service_error (recording failed).",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "default": _code(cfg, TOOL_SET_OUTCOME)},
            "product_id": {"type": "string", "description": "product id, for example p1"},
            "outcome": {"type": "string", "enum": outcomes_enum, "description": "One of: " + ", ".join(outcomes_enum)}},
            "required": ["product_id", "outcome"]}}}
    transfer_tool = _api_tool(api, "transfer_to_agent", {roles["customer_id"]: cust},
                              "Hand the call to a person. CALL it for: restructuring request (queue " + ir["transfer_queues"].get("restructuring", "") +
                              "), customer accepted the support team after hardship (queue " + ir["transfer_queues"].get("support", "") +
                              "), customer insists on a human (queue " + ir["transfer_queues"].get("human", "") + ").",
                              pre=_hook(cfg, hook_transfer_pre(roles)), post=_hook(cfg, HOOK_TRANSFER_POST), model_fields=[roles["transfer_queue"]])
    acct = api["tools"]["get_account"]
    check_tool = {"type": "function", "config": {"timeoutMs": 8000, "maxRetries": 0},
                  "function": {"name": "check_date", "handler": "__function__:api_call",
                               "description": "CALL it every time the customer names or changes a payment day, even vaguely, before you answer about that day. It answers immediately with DATE CHECK RESULT.",
                               "parameters": {"type": "object", "properties": {
                                   "url": {"type": "string", "default": acct["url"]}, "method": {"type": "string", "default": acct["method"]},
                                   "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                                   "body": {"type": "object", "properties": {
                                       "product_id": {"type": "string", "description": "product id, or all", "default": "{{product_id}}"},
                                       "customer_words": {"type": "string", "description": "the customer's exact words for the day", "default": "{{customer_words}}"},
                                       "date": {"type": "string", "description": "your reading of the day as YYYY-MM-DD", "default": "{{date}}"}},
                                       "required": ["product_id", "customer_words", "date"]}}}},
                  "pre_process_code": _hook(cfg, CHECK_DATE_PRE)}
    refusal_tool = {"type": "function", "config": {"timeoutMs": 8000, "maxRetries": 0},
                    "function": {"name": "record_refusal", "handler": "__function__:api_call",
                                 "description": "CALL it every time the customer flatly refuses or gives a non-answer for a product (including 'no' to paying today, or refusing an offer). It tells you the next offer, or that the attempts are used up.",
                                 "parameters": {"type": "object", "properties": {
                                     "url": {"type": "string", "default": acct["url"]}, "method": {"type": "string", "default": acct["method"]},
                                     "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                                     "body": {"type": "object", "properties": {
                                         "product_id": {"type": "string", "description": "the product the customer refused", "default": "{{product_id}}"},
                                         "refused": {"type": "string", "enum": ["today", "offer"], "description": "today = the first question about paying today; offer = an offer you proposed",
                                                     "default": "{{refused}}"}},
                                         "required": ["product_id", "refused"]}}}},
                    "pre_process_code": _hook(cfg, REFUSAL_PRE)}
    neg_rules = _rules(ir, "negotiation") + ("\n" + _rules(ir, "multi_product") if multi else "") + "\n" + _rules(ir, "confirmation") + "\n" + _rules(ir, "recording")
    nodes["negotiate"] = {
        "name": "negotiate", "node_type": "conversation",
        "on_enter": {"type": "tts", "text": "Thank you, you are verified. {{debt_statement}} {{first_ask}}"}, "on_enter_first_entry_only": True,
        "task_message": (
            "The customer is verified. You have stated the debt and asked the first question.\n"
            "PRODUCTS (amounts, floors, deadlines and persuasion steps are computed; use them exactly):\n{{facts_text}}\n\n"
            "Rules:\n" + neg_rules + "\n\n"
            "DATES: never calculate or judge a date yourself. Whenever the customer names or changes a day, call check_date first and follow its DATE CHECK RESULT exactly "
            "(it arrives looking like an error; that is normal).\nWeekday words:\n{{relative_dates}}\n\n"
            "TOOLS:\n"
            "- record_refusal: every flat refusal or non-answer for a product: refused=today for the first question about paying today, refused=offer for an offer you proposed. Propose exactly what its REFUSAL RESULT says; never count attempts yourself. When you move on to the next product, propose its attempt 1 directly and never ask about today again.\n"
            "- check_date: every time a day is named or changed (product id or all, the customer's exact words, your YYYY-MM-DD reading).\n"
            "- save_promises: once, after a clear yes to the read-back of all agreed promises. If it returns BLOCKED, follow the instruction. ACCEPTED means the system records them now.\n"
            "- set_product_outcome: for every product that ends without a promise.\n"
            "- transfer_to_agent: restructuring, accepted support, or insisted human; then the system says goodbye.\n"
            "- transition_to_wrap_up: when every product has a recorded promise or an outcome, and you have nothing left to say except goodbye. Do not say goodbye yourself; the system does."),
        "tools": [check_tool, refusal_tool, save_tool, set_outcome_tool, transfer_tool], "max_node_turns": 60, "max_turn_transition": "wrap_up",
        "transitions": [_when("plan_ready", "yes", "record_next", "confirmed promises to record"),
                        _when("transfer_done", "yes", "goodbye_transfer", "transfer done"),
                        _tool_transition("wrap_up", "Every product has a recorded promise or an outcome; the conversation is finished")]}
    # ---- sequential recording (no model involvement, verified: re-entry re-bakes values; engine retries HTTP 500)
    nodes["record_next"] = _code_node("record_next", cfg, ENTRY_RECORD_NEXT, [_when("rec_go", "yes", "write_promise", "record one promise"), _always("post_record")])
    rec_body = {roles["customer_id"]: cust, roles["promise_product"]: "{{cur_pid}}", roles["promise_amount"]: "{{cur_amount}}", roles["promise_date"]: "{{cur_date}}"}
    rec_tool = _api_tool(api, "create_promise", rec_body, max_retries=int(rec.get("retries_after_failure", 1)), post=_hook(cfg, HOOK_RECORD_POST))
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
            "When nothing is left, call transition_to_wrap_up. Do not say goodbye yourself; the system does."),
        "tools": [set_outcome_tool, transfer_tool], "max_node_turns": 10, "max_turn_transition": "wrap_up",
        "transitions": [_when("transfer_done", "yes", "goodbye_transfer", "transfer done"),
                        _tool_transition("wrap_up", "Nothing is left to discuss")]}
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
                               "pre_actions": [_api_tool(api, "add_note", note_body, max_retries=1)], "response_mapping": [],
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
                               "pre_actions": [_api_tool(api, "transfer_to_agent", {roles["customer_id"]: cust, roles["transfer_queue"]: human_q},
                                                         pre=_hook(cfg, hook_transfer_pre(roles)))],
                               "response_mapping": [], "transitions": [_always("goodbye_transfer")]}
    farewell = fs.get("farewell", "Thank you for your time. Goodbye.")
    after = rec.get("after_promise_sentence") or ""
    nodes["goodbye_promise"] = _end_node("goodbye_promise", "ended_promise", farewell)
    nodes["goodbye_plain"] = _end_node("goodbye_plain", "ended_no_promise", farewell)
    nodes["goodbye_failed"] = _end_node("goodbye_failed", "ended_not_verified", (idn.get("failed_message", "") + " " + farewell).strip())
    nodes["goodbye_deceased"] = _end_node("goodbye_deceased", "ended_deceased", (fs.get("deceased", "") + " Goodbye.").strip())
    nodes["goodbye_transfer"] = _end_node("goodbye_transfer", "ended_transferred", "I am connecting you now. " + farewell)

    close = _api_tool(api, "close_call", {roles["customer_id"]: cust, roles["close_outcome"]: "{{close_outcome}}", roles["close_reason"]: "{{close_reason}}"},
                      pre=_hook(cfg, hook_close_pre(roles)))
    close["config"]["alwaysRunsAt"] = ["end_of_call"]
    test_vars = {cv.get(k, k): v for k, v in {"customer_id": "c_test", "first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"}.items()}
    return {"initial_node": "init", "system_prompt": system_prompt, "nodes": nodes, "global_nodes": {}, "tools": [close], "test_variables": test_vars}
