// Cloudflare Worker glue (appended to core.js by build_worker.py). Every API request is routed to the Durable Object named after
// its session key, which runs the core with its own storage, one request at a time (strongly consistent per session).
// A "registry" object remembers which sessions exist, for the all-session ledger and reset.

const MOCK = compile(CONFIG);

function json(status, body) {
  const empty = status === 204 || status === 205 || status === 304;
  return new Response(empty ? null : JSON.stringify(body), {
    status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

async function readBody(request) {
  const text = await request.text();
  if (!text) return null;
  try { return JSON.parse(text); } catch { return { __unparseable__: text.slice(0, 200) }; }
}

function stub(env, name) { return env.MOCK_STATE.get(env.MOCK_STATE.idFromName(name)); }
async function call(env, name, msg) {
  const r = await stub(env, name).fetch("https://mock-state/", { method: "POST", body: JSON.stringify(msg) });
  return r.json();
}
const sessionObject = (sid) => "s:" + sid;
const REGISTRY = "registry";

// Durable Object storage behind the core's store interface. One object = one session.
class DOStore {
  constructor(storage) { this.storage = storage; }
  async load() { return (await this.storage.get("session")) || null; }
  async save(_sid, s) { await this.storage.put("session", s); }
  async appendLedger(_sid, entry) {
    const n = (await this.storage.get("n")) || 0;
    await this.storage.put({ ["l:" + String(n).padStart(9, "0")]: { ...entry, at: new Date().toISOString() }, n: n + 1 });
  }
  async ledger() { return [...(await this.storage.list({ prefix: "l:" })).values()]; }
  async reset() { await this.storage.deleteAll(); }
}

export class MockState {
  constructor(ctx, env) { this.ctx = ctx; this.env = env; this.store = new DOStore(ctx.storage); }

  async fetch(request) {
    const msg = await request.json();
    // blockConcurrencyWhile: no other request to this object interleaves, even across the registry call.
    const out = await this.ctx.blockConcurrencyWhile(() => this.run(msg));
    return new Response(JSON.stringify(out), { headers: { "content-type": "application/json" } });
  }

  async run(msg) {
    const st = this.ctx.storage;
    switch (msg.kind) {
      case "handle": {
        const fresh = (await st.get("registered")) !== true;
        const r = await handle(MOCK, msg.req, this.store);
        if (fresh && r.status !== 404) {
          await call(this.env, REGISTRY, { kind: "register", sid: msg.sid });
          await st.put("registered", true);
        }
        return r;
      }
      case "ledger": return { ledger: await this.store.ledger() };
      case "reset": await this.store.reset(); return { ok: true };
      case "register": await st.put("sid:" + msg.sid, true); return { ok: true };
      case "sessions": return { sessions: [...(await st.list({ prefix: "sid:" })).keys()].map((k) => k.slice(4)) };
      case "clear": await st.deleteAll(); return { ok: true };
      default: return { error: "unknown message" };
    }
  }
}

async function control(path, method, url, request, env) {
  if (path === "/__mock/health") {
    return json(200, { ok: true, operations: Object.keys(MOCK.byKey).sort(), base_path: MOCK.basePath || null,
                       session_key: MOCK.behaviour.session_key || null });
  }
  if (path === "/__mock/ledger") {
    const sid = url.searchParams.get("session");
    if (sid !== null) return json(200, { ledger: (await call(env, sessionObject(sid), { kind: "ledger" })).ledger });
    const { sessions } = await call(env, REGISTRY, { kind: "sessions" });
    const all = {};
    for (const s of sessions) all[s] = (await call(env, sessionObject(s), { kind: "ledger" })).ledger;
    return json(200, { ledger: all });
  }
  if (path === "/__mock/reset" && method === "POST") {
    const body = await readBody(request);
    const sid = body && typeof body === "object" && body.session !== undefined && body.session !== null ? String(body.session) : null;
    if (sid !== null) {
      await call(env, sessionObject(sid), { kind: "reset" });
    } else {
      const { sessions } = await call(env, REGISTRY, { kind: "sessions" });
      for (const s of sessions) await call(env, sessionObject(s), { kind: "reset" });
      await call(env, REGISTRY, { kind: "clear" });
    }
    return json(200, { ok: true });
  }
  return json(404, { error: `unknown control endpoint ${method} ${path}` });
}

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      const method = request.method.toUpperCase();
      const path = url.pathname;
      const bare = stripBase(MOCK, path);
      if (bare.startsWith("/__mock/")) return await control(bare, method, url, request, env);
      const found = findOp(MOCK, method, path);
      if (!found) return json(404, { error: `no operation ${method} ${path} in the contract` });
      const headers = {};
      for (const [k, v] of request.headers) headers[k.toLowerCase()] = v;
      const body = await readBody(request);
      const query = Object.fromEntries(url.searchParams);
      const sid = sessionId(MOCK, headers, body, query, found.params);
      const r = await call(env, sessionObject(sid), { kind: "handle", sid, req: { method, path, headers, body, query } });
      return json(r.status, r.body);
    } catch (e) {   // a mock bug must still answer JSON
      return json(500, { error: `mock error: ${e && e.name}: ${e && e.message}` });
    }
  },
};
