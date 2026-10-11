// Pure request-handling core of the Cloudflare mock: a JavaScript port of devtools/mock_service.py (same contract + behaviour
// semantics). No imports, no I/O: state lives behind an async `store` (in-memory in tests, Durable Object storage in the Worker).
// build_worker.py inlines this file into the generated worker.js (the `export` keywords are stripped there).
//
// config = {ops: {key: {method, path, operation_id, request_schema, body_required, response_schemas}}, behaviour, base_path}
// store  = {load(sid) -> {state, hits} | null, save(sid, s), appendLedger(sid, entry), ledger(sid) -> [entry], reset(sid)}

export class MockConfigError extends Error {}

// ---- formats (same rules as mock_service.FORMATS: only date, time, date-time are checked) ---------------------------------
const DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
function validDate(y, m, d) {
  if (m < 1 || m > 12 || d < 1) return false;
  const leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
  return d <= (m === 2 && leap ? 29 : DAYS[m - 1]) && y >= 1;
}
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d(\.\d+)?)?(Z|[+-]([01]\d|2[0-3]):[0-5]\d)?$/;
const DT_RE = /^(\d{4})-(\d{2})-(\d{2})(?:[Tt ](\d{2})(?::?(\d{2})(?::?(\d{2})(?:[.,]\d+)?)?)?(?:[Zz]|[+-](\d{2})(?::?(\d{2})(?::?\d{2}(?:\.\d+)?)?)?)?)?$/;
export const FORMATS = {
  date(v) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
    return !!m && validDate(+m[1], +m[2], +m[3]);
  },
  time(v) { return TIME_RE.test(v); },
  "date-time"(v) {
    const m = DT_RE.exec(v);
    if (!m || !validDate(+m[1], +m[2], +m[3])) return false;
    if (m[4] !== undefined && +m[4] > 23) return false;
    if (m[5] !== undefined && +m[5] > 59) return false;
    if (m[6] !== undefined && +m[6] > 59) return false;
    if (m[7] !== undefined && +m[7] > 23) return false;
    if (m[8] !== undefined && +m[8] > 59) return false;
    return true;
  },
};

// ---- a small, dependency-free JSON Schema (2020-12 subset) validator ------------------------------------------------------
function repr(v) {
  if (typeof v === "string") return `'${v}'`;
  if (v === true) return "True";
  if (v === false) return "False";
  if (v === null || v === undefined) return "None";
  if (Array.isArray(v)) return "[" + v.map(repr).join(", ") + "]";
  if (typeof v === "object") return "{" + Object.entries(v).map(([k, x]) => `${repr(k)}: ${repr(x)}`).join(", ") + "}";
  return String(v);
}
function isObj(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }
export function deepEqual(a, b) {
  if (a === b) return true;
  if (Array.isArray(a)) return Array.isArray(b) && a.length === b.length && a.every((x, i) => deepEqual(x, b[i]));
  if (isObj(a) && isObj(b)) {
    const ka = Object.keys(a), kb = Object.keys(b);
    return ka.length === kb.length && ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && deepEqual(a[k], b[k]));
  }
  return false;
}
function typeOk(t, v) {
  switch (t) {
    case "object": return isObj(v);
    case "array": return Array.isArray(v);
    case "string": return typeof v === "string";
    case "integer": return typeof v === "number" && Number.isInteger(v);
    case "number": return typeof v === "number" && Number.isFinite(v);
    case "boolean": return typeof v === "boolean";
    case "null": return v === null;
    default: return true;
  }
}
function regex(p) {
  try { return new RegExp(p, "u"); } catch { try { return new RegExp(p); } catch { return null; } }
}
function strLen(s) { return [...s].length; }

export function validate(schema, v, formats = true, path = [], errs = []) {
  if (schema === false) { errs.push({ path, message: `False schema does not allow ${repr(v)}` }); return errs; }
  if (!isObj(schema)) return errs;
  const err = (message, p = path) => errs.push({ path: p, message });
  if ("type" in schema) {
    const ts = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!ts.some((t) => typeOk(t, v))) err(`${repr(v)} is not of type ${ts.map(repr).join(", ")}`);
  }
  if (Array.isArray(schema.enum) && !schema.enum.some((e) => deepEqual(e, v))) err(`${repr(v)} is not one of ${repr(schema.enum)}`);
  if ("const" in schema && !deepEqual(schema.const, v)) err(`${repr(schema.const)} was expected`);
  if (isObj(v)) {
    for (const r of Array.isArray(schema.required) ? schema.required : []) {
      if (!Object.prototype.hasOwnProperty.call(v, r)) err(`${repr(r)} is a required property`);
    }
    const props = isObj(schema.properties) ? schema.properties : {};
    for (const [k, sub] of Object.entries(props)) {
      if (Object.prototype.hasOwnProperty.call(v, k)) validate(sub, v[k], formats, path.concat([k]), errs);
    }
    const pats = isObj(schema.patternProperties) ? Object.entries(schema.patternProperties).map(([p, s]) => [regex(p), s]) : [];
    for (const [k, val] of Object.entries(v)) {
      for (const [re, s] of pats) if (re && re.test(k)) validate(s, val, formats, path.concat([k]), errs);
    }
    if ("additionalProperties" in schema) {
      const extra = Object.keys(v).filter((k) => !(k in props) && !pats.some(([re]) => re && re.test(k)));
      if (schema.additionalProperties === false && extra.length) {
        err(`Additional properties are not allowed (${extra.map(repr).join(", ")} ${extra.length > 1 ? "were" : "was"} unexpected)`);
      } else if (isObj(schema.additionalProperties)) {
        for (const k of extra) validate(schema.additionalProperties, v[k], formats, path.concat([k]), errs);
      }
    }
    const n = Object.keys(v).length;
    if (typeof schema.minProperties === "number" && n < schema.minProperties) err(`${repr(v)} does not have enough properties`);
    if (typeof schema.maxProperties === "number" && n > schema.maxProperties) err(`${repr(v)} has too many properties`);
  }
  if (Array.isArray(v)) {
    const prefix = Array.isArray(schema.prefixItems) ? schema.prefixItems : [];
    prefix.forEach((s, i) => { if (i < v.length) validate(s, v[i], formats, path.concat([i]), errs); });
    if (isObj(schema.items) || schema.items === false) {
      for (let i = prefix.length; i < v.length; i++) validate(schema.items, v[i], formats, path.concat([i]), errs);
    }
    if (typeof schema.minItems === "number" && v.length < schema.minItems) err(`${repr(v)} should be non-empty`);
    if (typeof schema.maxItems === "number" && v.length > schema.maxItems) err(`${repr(v)} is too long`);
    if (schema.uniqueItems === true && v.some((x, i) => v.slice(i + 1).some((y) => deepEqual(x, y)))) err(`${repr(v)} has non-unique elements`);
  }
  if (typeof v === "string") {
    if (typeof schema.minLength === "number" && strLen(v) < schema.minLength) err(`${repr(v)} is too short`);
    if (typeof schema.maxLength === "number" && strLen(v) > schema.maxLength) err(`${repr(v)} is too long`);
    if (typeof schema.pattern === "string") {
      const re = regex(schema.pattern);
      if (re && !re.test(v)) err(`${repr(v)} does not match ${repr(schema.pattern)}`);
    }
    if (formats && typeof schema.format === "string" && FORMATS[schema.format] && !FORMATS[schema.format](v)) {
      err(`${repr(v)} is not a ${repr(schema.format)}`);
    }
  }
  if (typeof v === "number") {
    if (typeof schema.minimum === "number" && v < schema.minimum) err(`${v} is less than the minimum of ${schema.minimum}`);
    if (typeof schema.maximum === "number" && v > schema.maximum) err(`${v} is greater than the maximum of ${schema.maximum}`);
    if (typeof schema.exclusiveMinimum === "number" && v <= schema.exclusiveMinimum) {
      err(`${v} is less than or equal to the minimum of ${schema.exclusiveMinimum}`);
    }
    if (typeof schema.exclusiveMaximum === "number" && v >= schema.exclusiveMaximum) {
      err(`${v} is greater than or equal to the maximum of ${schema.exclusiveMaximum}`);
    }
    if (typeof schema.multipleOf === "number" && schema.multipleOf > 0) {
      const q = v / schema.multipleOf;
      if (Math.abs(q - Math.round(q)) > 1e-9) err(`${v} is not a multiple of ${schema.multipleOf}`);
    }
  }
  if (Array.isArray(schema.allOf)) for (const s of schema.allOf) validate(s, v, formats, path, errs);
  const ok = (s) => validate(s, v, formats, path, []).length === 0;
  if (Array.isArray(schema.anyOf) && !schema.anyOf.some(ok)) err(`${repr(v)} is not valid under any of the given schemas`);
  if (Array.isArray(schema.oneOf)) {
    const n = schema.oneOf.filter(ok).length;
    if (n === 0) err(`${repr(v)} is not valid under any of the given schemas`);
    else if (n > 1) err(`${repr(v)} is valid under each of more than one of the given schemas`);
  }
  if ("not" in schema && ok(schema.not)) err(`${repr(v)} should not be valid under ${repr(schema.not)}`);
  return errs;
}

// ---- templates and rule matching ------------------------------------------------------------------------------------------
export function lookup(expr, ctx) {
  let cur = ctx;
  for (const part of expr.split(".")) {
    if (isObj(cur)) cur = Object.prototype.hasOwnProperty.call(cur, part) ? cur[part] : null;
    else if (Array.isArray(cur) && /^\d+$/.test(part) && +part < cur.length) cur = cur[+part];
    else return null;
  }
  return cur === undefined ? null : cur;
}
const clone = (v) => (v === undefined ? null : JSON.parse(JSON.stringify(v)));
const FULL = /^\{\{\s*([\p{L}\p{N}_.$]+)\s*\}\}$/u;
const PART = /\{\{\s*([\p{L}\p{N}_.$]+)\s*\}\}/gu;
function pyStr(v) {
  if (typeof v === "string") return v;
  if (typeof v === "boolean") return v ? "True" : "False";
  if (typeof v === "number") return String(v);
  return JSON.stringify(v);
}
export function render(value, ctx) {
  if (typeof value === "string") {
    const m = FULL.exec(value);
    if (m) return clone(lookup(m[1], ctx));
    return value.replace(PART, (_, key) => {
      const got = lookup(key, ctx);
      if (got === null) throw new MockConfigError(`template key ${key} is missing`);
      return pyStr(got);
    });
  }
  if (Array.isArray(value)) return value.map((v) => render(v, ctx));
  if (isObj(value)) return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, render(v, ctx)]));
  return value;
}
export function matches(when, ctx) {
  for (const [key, want] of Object.entries(when || {})) {
    const got = key.startsWith("$") ? lookup(key.slice(1), ctx) : lookup("body." + key, ctx);
    if (isObj(want) && "in" in want) {
      if (!(Array.isArray(want.in) && want.in.some((w) => deepEqual(w, got)))) return false;
    } else if (!deepEqual(got, want)) return false;
  }
  return true;
}

// ---- the mock -------------------------------------------------------------------------------------------------------------
function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
function pathRegex(path) {
  const names = [];
  const src = path.split(/(\{[^/}]+\})/).map((part) => {
    if (part.startsWith("{") && part.endsWith("}")) { names.push(part.slice(1, -1)); return "([^/]+)"; }
    return escapeRe(part);
  }).join("");
  return { re: new RegExp("^" + src + "$"), names };
}

export function compile(config) {
  const behaviour = config.behaviour || {};
  const ops = Object.entries(config.ops || {}).map(([key, op]) => ({ key, op, ...pathRegex(op.path) }));
  ops.sort((a, b) => (a.op.path.match(/\{/g) || []).length - (b.op.path.match(/\{/g) || []).length);   // concrete paths first
  return { ops, byKey: config.ops || {}, behaviour, basePath: (config.base_path || "").replace(/\/+$/, "") };
}

function findExact(mock, method, path) {
  for (const o of mock.ops) {
    if (o.op.method !== method) continue;
    const m = o.re.exec(path);
    if (m) {
      const params = {};
      o.names.forEach((n, i) => { try { params[n] = decodeURIComponent(m[i + 1]); } catch { params[n] = m[i + 1]; } });
      return { key: o.key, op: o.op, params, path };
    }
  }
  return null;
}

// The contract's paths, as served; a request may also carry the contract server's base path (e.g. /v1/close_call).
export function findOp(mock, method, path) {
  return findExact(mock, method, path)
    || (mock.basePath && path.startsWith(mock.basePath + "/") ? findExact(mock, method, path.slice(mock.basePath.length)) : null);
}

export function stripBase(mock, path) {
  return mock.basePath && path.startsWith(mock.basePath + "/") ? path.slice(mock.basePath.length) : path;
}

export function sessionId(mock, headers, body, query, params) {
  const key = mock.behaviour.session_key || {};
  const s = (v) => (v === null || v === undefined || v === "" || v === false || v === 0 ? "default" : pyStr(v));
  if ("header" in key) {
    const want = String(key.header).toLowerCase();
    const hit = Object.entries(headers || {}).find(([k]) => k.toLowerCase() === want);
    return (hit && hit[1]) || "default";
  }
  if ("query" in key) return s((query || {})[key.query]);
  if ("path" in key) return s((params || {})[key.path]);
  if ("body" in key && isObj(body)) return s(body[key.body]);
  return "default";
}

export function freshSession(mock, sid) {
  const fx = mock.behaviour.fixtures || {};
  return { state: clone(sid in fx ? fx[sid] : (fx["*"] || {})), hits: {} };
}

export function responseProblem(op, status, resp) {
  const rs = op.response_schemas || {};
  let schema, found = false;
  for (const code of [String(status), `${String(status)[0]}XX`, "default"]) {
    if (code in rs) { schema = rs[code]; found = true; break; }
  }
  if (!found) return Object.keys(rs).length ? `status ${status} is not declared by the contract` : null;
  if (schema === null || schema === undefined) return null;
  const e = validate(schema, resp, false)[0];
  return e ? `${status} response does not match the contract: ${e.message}` : null;
}

// One API request -> {status, body}. Every answered request is written to the session ledger (404 has no session).
export async function handle(mock, req, store) {
  const method = String(req.method || "GET").toUpperCase();
  const found = findOp(mock, method, req.path);
  if (!found) return { status: 404, body: { error: `no operation ${method} ${req.path} in the contract` } };
  const { key, op, params } = found;
  const body = req.body === undefined ? null : req.body;
  const query = req.query || {};
  const sid = sessionId(mock, req.headers, body, query, params);
  try {
    return await handleOp(mock, key, op, sid, method, found.path, body, query, params, store);
  } catch (e) {
    if (e instanceof MockConfigError) return { status: 500, body: { error: `mock misconfigured: ${e.message}` } };
    throw e;
  }
}

async function handleOp(mock, key, op, sid, method, path, body, query, params, store) {
  const s = (await store.load(sid)) || freshSession(mock, sid);
  const entry = { op: key, method, path, body, session: sid };
  const reject = async (msg) => {
    Object.assign(entry, { status: 422, response: { error: msg }, rejected: true });
    await store.save(sid, s);
    await store.appendLedger(sid, entry);
    return { status: 422, body: { error: msg } };
  };
  if (body === null && op.body_required) return reject("request body required");
  if (op.request_schema !== null && op.request_schema !== undefined && body !== null) {
    const errors = validate(op.request_schema, body, true);
    if (errors.length) {
      errors.sort((a, b) => {   // stable, by path (as mock_service sorts by e.path)
        const pa = a.path.map(String), pb = b.path.map(String);
        for (let i = 0; i < Math.min(pa.length, pb.length); i++) if (pa[i] !== pb[i]) return pa[i] < pb[i] ? -1 : 1;
        return pa.length - pb.length;
      });
      return reject(errors.slice(0, 5).map((e) => `${e.path.join("/") || "(body)"}: ${e.message}`).join("; "));
    }
  }
  const ctx = { body: body === null ? {} : body, state: s.state, session: sid, query, path: params };
  const rules = (mock.behaviour.operations || {})[key] || [];
  const idx = rules.findIndex((r) => matches(r.when, ctx));
  let status, resp;
  if (idx < 0) {
    status = 501; resp = { error: `mock has no behaviour for ${key} with this request` };
  } else {
    const rule = rules[idx];
    const rid = `${key}#${idx}`;
    s.hits[rid] = (s.hits[rid] || 0) + 1;
    if (s.hits[rid] <= parseInt(rule.fail_first || 0, 10)) {
      status = 500; resp = { error: "injected failure" };
    } else {
      status = parseInt(rule.status === undefined ? 200 : rule.status, 10);
      resp = render(rule.body === undefined ? {} : rule.body, ctx);
      const problem = responseProblem(op, status, resp);
      if (problem) {
        status = 500; resp = { error: "mock misconfigured: " + problem };
      } else {
        for (const [k, v] of Object.entries(rule.set || {})) s.state[k] = render(v, ctx);
        for (const [k, v] of Object.entries(rule.append || {})) {
          if (!(k in s.state)) s.state[k] = [];
          if (!Array.isArray(s.state[k])) throw new MockConfigError(`append target state.${k} is not a list`);
          s.state[k].push(render(v, ctx));
        }
      }
    }
    entry.recorded = !!rule.record && status < 400;
  }
  Object.assign(entry, { status, response: resp });
  await store.save(sid, s);
  await store.appendLedger(sid, entry);
  return { status, body: resp };
}

// In-memory store (node tests; same interface as the Durable Object store).
export class MemoryStore {
  constructor() { this.sessions = new Map(); this.ledgers = new Map(); }
  async load(sid) { return this.sessions.has(sid) ? clone(this.sessions.get(sid)) : null; }
  async save(sid, s) { this.sessions.set(sid, clone(s)); }
  async appendLedger(sid, entry) {
    if (!this.ledgers.has(sid)) this.ledgers.set(sid, []);
    this.ledgers.get(sid).push(clone(entry));
  }
  async ledger(sid) { return clone(this.ledgers.get(sid) || []); }
  async reset(sid) {
    if (sid === null || sid === undefined) { this.sessions.clear(); this.ledgers.clear(); }
    else { this.sessions.delete(sid); this.ledgers.delete(sid); }
  }
}
