// Local test harness: runs a scenario (a list of steps) against the mock under node, with no Cloudflare account.
//
//   node harness.mjs core   <core.js> <config.json> <scenario.json>   # the pure core with an in-memory store
//   node harness.mjs worker <worker.js> <scenario.json>               # the GENERATED worker: its fetch handler + the MockState
//                                                                      # Durable Object class, over an in-memory DO storage fake
// step = {method, path, headers?, body?, query?}            -> [status, body]
//      | {control: "reset", session?} | {control: "ledger", session?} | {control: "health"}
// Steps listed in {"parallel": [step, ...]} are sent concurrently (worker mode). Prints the list of results as JSON.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [mode, modulePath, a3, a4] = process.argv.slice(2);
const scenario = JSON.parse(readFileSync(mode === "core" ? a4 : a3, "utf8"));
const mod = await import(pathToFileURL(modulePath).href);

// ---- fakes of the Durable Object runtime ----------------------------------------------------------------------------------
class FakeStorage {
  constructor() { this.m = new Map(); }
  async get(k) { return this.m.has(k) ? structuredClone(this.m.get(k)) : undefined; }
  async put(k, v) {
    if (typeof k === "object") { for (const [kk, vv] of Object.entries(k)) this.m.set(kk, structuredClone(vv)); }
    else this.m.set(k, structuredClone(v));
  }
  async delete(k) { return this.m.delete(k); }
  async deleteAll() { this.m.clear(); }
  async list({ prefix = "" } = {}) {
    return new Map([...this.m.keys()].filter((k) => k.startsWith(prefix)).sort().map((k) => [k, structuredClone(this.m.get(k))]));
  }
}
class FakeCtx {
  constructor() { this.storage = new FakeStorage(); this.chain = Promise.resolve(); }
  blockConcurrencyWhile(fn) {   // serialize like the runtime: the next callback starts after the previous one settles
    const run = this.chain.then(() => fn());
    this.chain = run.catch(() => {});
    return run;
  }
}
function fakeNamespace(Cls) {
  const objects = new Map();
  const env = {};
  env.MOCK_STATE = {
    idFromName: (name) => ({ name }),
    get: (id) => {
      if (!objects.has(id.name)) objects.set(id.name, new Cls(new FakeCtx(), env));
      const obj = objects.get(id.name);
      return { fetch: (url, init) => obj.fetch(new Request(url, init)) };
    },
  };
  env.objects = objects;
  return env;
}

const strip = (v) => {   // drop the worker's wall-clock 'at' from ledger entries for comparison
  if (Array.isArray(v)) return v.map(strip);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).filter(([k]) => k !== "at").map(([k, x]) => [k, strip(x)]));
  return v;
};

let runStep;
if (mode === "core") {
  const config = JSON.parse(readFileSync(a3, "utf8"));
  const mock = mod.compile(config);
  const store = new mod.MemoryStore();
  runStep = async (s) => {
    if (s.control === "reset") { await store.reset(s.session ?? null); return [200, { ok: true }]; }
    if (s.control === "ledger") return [200, { ledger: await store.ledger(s.session) }];
    if (s.control === "health") return [200, { ok: true, operations: Object.keys(config.ops).sort() }];
    const r = await mod.handle(mock, { method: s.method, path: s.path, headers: s.headers || {}, body: s.body ?? null, query: s.query || {} }, store);
    return [r.status, JSON.parse(JSON.stringify(r.body))];
  };
} else {
  const env = fakeNamespace(mod.MockState);
  const BASE = "https://bank-mock.example.workers.dev";
  runStep = async (s) => {
    let req;
    if (s.control) {
      const q = s.control === "ledger" && s.session !== undefined && s.session !== null ? `?session=${encodeURIComponent(s.session)}` : "";
      req = s.control === "reset"
        ? new Request(`${BASE}/__mock/reset`, { method: "POST", body: JSON.stringify({ session: s.session ?? null }) })
        : new Request(`${BASE}/__mock/${s.control}${q}`);
    } else {
      const qs = s.query ? "?" + new URLSearchParams(s.query).toString() : "";
      const init = { method: s.method, headers: { "content-type": "application/json", ...(s.headers || {}) } };
      if (s.body !== undefined && s.body !== null) init.body = s.raw ? s.body : JSON.stringify(s.body);
      req = new Request(BASE + s.path + qs, init);
    }
    const res = await mod.default.fetch(req, env);
    const text = await res.text();
    return [res.status, strip(text ? JSON.parse(text) : null), res.headers.get("content-type")];
  };
}

const out = [];
for (const s of scenario) {
  if (s.parallel) out.push(await Promise.all(s.parallel.map(runStep)));
  else out.push(await runStep(s));
}
process.stdout.write(JSON.stringify(out));
