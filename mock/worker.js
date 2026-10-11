// Freya Bank statement / debt services mock (Cloudflare Worker, ES module, no deps).
// Contract: sources/documents/2026-10-01-service-contract.md (sections 2-6).
// Answers: test-environment/test-customers.json, embedded in data.js (generated).
// Responses are stateless and exactly the pack's bodies + HTTP statuses; write calls
// (statement.week.change, statement.pdf.send) are additionally appended to a per-customer
// ledger in a SQLite-backed Durable Object so tests can verify what the agent did.

import DATA from "./data.js";

const CUSTOMERS = DATA.customers;
const CARDS = DATA.cards;
const PREFIX = "/voice/v1/";
const SERVICES = [
  "cards.list",
  "cards.balance",
  "statement.week.get",
  "statement.week.decide",
  "statement.week.change",
  "contact.email.get",
  "statement.pdf.send",
];
const WRITE_SERVICES = new Set(["statement.week.change", "statement.pdf.send"]);
const ERROR_ORIGIN = "ivr-gateway-test:ivr-channel-v0";
const NOT_FOUND_CAUSE = "com.freyabank.ivrgateway.ivrchannel.rest.exception.NotFoundException";
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "*",
};
const JSON_HEADERS = { "Content-Type": "application/json; charset=UTF-8", ...CORS };

// Fallbacks for the Freya panel's "Test API Request" button, which sends "{{var}}" unfilled
// (cloudflare-mock/README.md). Real calls never hit these.
const TEMPLATE_CUSTOMER = "90000001";
const TEMPLATE_CARD = "540000******1234";
const TEMPLATE_WEEK = "2";

// ---------- helpers ----------

function rawResponse(text, status) {
  return new Response(text, { status, headers: JSON_HEADERS });
}

function json(obj, status = 200) {
  return rawResponse(JSON.stringify(obj), status);
}

function guid() {
  return crypto.randomUUID().replace(/-/g, "");
}

// Error envelope, contract section 4: no `data`, only `result`.
function errorBody(code, type, message, rootCause) {
  const g = guid();
  return {
    result: {
      guid: g,
      returnCode: code,
      messageText: message,
      code,
      message,
      type,
      details: { guid: g, rootCause, errorOrigin: ERROR_ORIGIN },
    },
  };
}

const err = {
  invalid: (message) => ({ http: 400, obj: errorBody("INVALID_ARGUMENTS_ERROR", "ERROR", message, "InvalidArgumentsException") }),
  noCard: () => ({ http: 404, obj: errorBody("FB_IVR_CHANNEL_102", "WARNING", "Müşteriye ait geçerli kart bilgisi bulunamadı.", NOT_FOUND_CAUSE) }),
  balanceNotFound: () => ({ http: 404, obj: errorBody("FB_IVR_CHANNEL_120", "WARNING", "Kart bakiye servisi hatası: kart bulunamadı", NOT_FOUND_CAUSE) }),
  weekNotFound: () => ({ http: 404, obj: errorBody("FB_IVR_CHANNEL_121", "WARNING", "Ekstre hafta servisi hatası: kart bulunamadı", NOT_FOUND_CAUSE) }),
  noPdf: () => ({ http: 404, obj: errorBody("FB_IVR_CHANNEL_107", "WARNING", "Pdf dokümanı bulunamadı", NOT_FOUND_CAUSE) }),
};

function isTemplate(v) {
  return typeof v === "string" && (v.trim().startsWith("{{") || v.trim().startsWith("%7B%7B"));
}

function isEmpty(v) {
  return v === undefined || v === null || (typeof v === "string" && v.trim() === "");
}

function normCustomer(v) {
  if (isTemplate(v)) return TEMPLATE_CUSTOMER;
  return String(v).trim();
}

// Exact masked number as cards.list gave it. Lenient fallback only when the exact key is
// unknown: match on first 6 + last 4 digits (e.g. a full 16-digit number, as production
// will send per the 9 Oct recap), if that identifies exactly one card.
function resolveCard(v) {
  if (isTemplate(v)) return TEMPLATE_CARD;
  const s = String(v).trim();
  if (CARDS[s]) return s;
  const compact = s.replace(/[\s-]/g, "");
  if (CARDS[compact]) return compact;
  if (/^\d{6}[\d*]{6}\d{4}$/.test(compact)) {
    const hits = Object.keys(CARDS).filter(
      (k) => k.slice(0, 6) === compact.slice(0, 6) && k.slice(-4) === compact.slice(-4),
    );
    if (hits.length === 1) return hits[0];
  }
  return s;
}

function normWeek(v) {
  if (isTemplate(v)) return TEMPLATE_WEEK;
  return String(v).trim();
}

// ---------- services (contract section 5, lookup rules from test-customers.md) ----------

function fromEntry(e) {
  return { http: e.http, raw: e.body, delay: e.delay || 0 };
}

const handlers = {
  "cards.list"(d) {
    if (isEmpty(d.customerNo)) return err.invalid("customer number cannot be empty");
    const c = CUSTOMERS[normCustomer(d.customerNo)];
    return c ? fromEntry(c.cardsList) : err.noCard();
  },
  "cards.balance"(d) {
    if (isEmpty(d.cardNumber)) return err.invalid("card number cannot be empty");
    const card = CARDS[resolveCard(d.cardNumber)];
    return card && card.balance ? fromEntry(card.balance) : err.balanceNotFound();
  },
  "statement.week.get"(d) {
    if (isEmpty(d.cardNumber)) return err.invalid("card number cannot be empty");
    const card = CARDS[resolveCard(d.cardNumber)];
    return card && card.weekGet ? fromEntry(card.weekGet) : err.weekNotFound();
  },
  "statement.week.decide"(d) {
    return weekLookup(d, "decide");
  },
  "statement.week.change"(d) {
    return weekLookup(d, "change");
  },
  "contact.email.get"(d) {
    if (isEmpty(d.customerNo)) return err.invalid("customer number cannot be empty");
    if (isEmpty(d.intAddressInfoType)) return err.invalid("intAddressInfoType cannot be empty");
    const c = CUSTOMERS[normCustomer(d.customerNo)];
    // No 404 for this service in section 4 -> 400 for an unknown customer (section 6).
    return c && c.contact ? fromEntry(c.contact) : err.invalid("customer not found");
  },
  "statement.pdf.send"(d) {
    for (const f of ["customerNo", "cardNumber", "currencyType", "statementDate", "emailAddress"]) {
      if (isEmpty(d[f])) return err.invalid(`${f} cannot be empty`);
    }
    const cur = String(d.currencyType).trim().toUpperCase();
    if (!["TL", "TRY", "USD", "EUR", "GBP"].includes(cur)) return err.invalid("currencyType must be one of TL, USD, EUR, GBP");
    const c = CUSTOMERS[normCustomer(d.customerNo)];
    return c && c.pdf ? fromEntry(c.pdf) : err.noPdf();
  },
};

function weekLookup(d, kind) {
  if (isEmpty(d.cardNumber)) return err.invalid("card number cannot be empty");
  if (isEmpty(d.newStatementWeek)) return err.invalid("newStatementWeek cannot be empty");
  const week = normWeek(d.newStatementWeek);
  if (!["1", "2", "3", "4"].includes(week)) return err.invalid("newStatementWeek must be between 1 and 4");
  const card = CARDS[resolveCard(d.cardNumber)];
  // decide/change have no 404 in section 4 -> 400 for unknown card or the card's current week.
  if (!card) return err.invalid("card not found");
  const e = card[kind][week];
  return e ? fromEntry(e) : err.invalid("newStatementWeek is the card's current statement week");
}

// ---------- ledger (Durable Object) ----------

function ledgerCustomer(service, d) {
  if (service === "statement.week.change") {
    const card = CARDS[resolveCard(d.cardNumber ?? "")];
    return card ? card.customerNo : "unknown";
  }
  return isEmpty(d.customerNo) ? "unknown" : normCustomer(d.customerNo);
}

function stub(env, customer) {
  return env.STATE.get(env.STATE.idFromName(String(customer)));
}

async function appendLedger(env, customer, record) {
  if (!env || !env.STATE) return;
  try {
    await stub(env, customer).fetch("https://do/append", { method: "POST", body: JSON.stringify(record) });
  } catch (e) {
    console.log("ledger append failed", String(e));
  }
}

export class CustomerState {
  constructor(state) {
    this.sql = state.storage.sql;
    this.sql.exec(
      "CREATE TABLE IF NOT EXISTS ledger (seq INTEGER PRIMARY KEY AUTOINCREMENT, record TEXT NOT NULL)",
    );
  }

  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === "/append") {
      this.sql.exec("INSERT INTO ledger (record) VALUES (?)", await request.text());
      return json({ ok: true });
    }
    if (path === "/list") {
      const rows = this.sql.exec("SELECT seq, record FROM ledger ORDER BY seq").toArray();
      return json(rows.map((r) => ({ seq: r.seq, ...JSON.parse(r.record) })));
    }
    if (path === "/reset") {
      this.sql.exec("DELETE FROM ledger");
      return json({ ok: true });
    }
    return json({ error: "not found" }, 404);
  }
}

// ---------- routing ----------

async function handleService(service, request, env) {
  let body;
  try {
    const text = await request.text();
    body = text.trim() === "" ? {} : JSON.parse(text);
  } catch {
    const e = err.invalid("request body is not valid JSON");
    return json(e.obj, e.http);
  }
  if (!body || typeof body !== "object") body = {};
  // Envelope per section 3 is {data, call}; tolerate bare fields if `data` is absent.
  const d = body.data && typeof body.data === "object" ? body.data : body;

  const out = handlers[service](d);
  if (out.delay) await new Promise((r) => setTimeout(r, out.delay * 1000));
  const text = out.raw !== undefined ? out.raw : JSON.stringify(out.obj);

  if (WRITE_SERVICES.has(service)) {
    await appendLedger(env, ledgerCustomer(service, d), {
      ts: new Date().toISOString(),
      service,
      request: { data: d, call: body.call ?? null },
      http: out.http,
      response: JSON.parse(text),
    });
  }
  return rawResponse(text, out.http);
}

function health(env) {
  return json({
    status: "ok",
    service: "freya-bank-mock",
    customers: Object.keys(CUSTOMERS).length,
    cards: Object.keys(CARDS).length,
    ledger: Boolean(env && env.STATE),
    routes: [
      ...SERVICES.map((s) => `POST ${PREFIX}${s}`),
      "POST /oauth2/token",
      "GET /__mock/health",
      "GET /__mock/ledger?customer=<customerNo>",
      "POST /__mock/reset[?customer=<customerNo>]",
    ],
  });
}

async function ledger(url, env) {
  const customer = url.searchParams.get("customer");
  if (!customer) return json({ error: "customer query parameter required" }, 400);
  if (!env || !env.STATE) return json({ error: "ledger not configured" }, 503);
  const res = await stub(env, customer.trim()).fetch("https://do/list");
  return json({ customer: customer.trim(), entries: await res.json() });
}

async function reset(url, env) {
  if (!env || !env.STATE) return json({ error: "ledger not configured" }, 503);
  const one = url.searchParams.get("customer");
  const targets = one ? [one.trim()] : [...Object.keys(CUSTOMERS), "unknown"];
  await Promise.all(targets.map((c) => stub(env, c).fetch("https://do/reset", { method: "POST" })));
  return json({ ok: true, reset: one ? targets : "all" });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    const method = request.method;

    if (method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

    if (path === "/__mock/health" || path === "/health" || path === "/") {
      return method === "GET" || method === "HEAD" ? health(env) : json({ error: "method not allowed" }, 405);
    }
    if (path === "/__mock/ledger" && method === "GET") return ledger(url, env);
    if (path === "/__mock/reset" && method === "POST") return reset(url, env);

    // Section 2: token endpoint. Not checked in test; always issues a token.
    if (path === "/oauth2/token") {
      if (method !== "POST") return json({ error: "method not allowed" }, 405);
      return json({ access_token: `mock-${guid()}`, token_type: "Bearer", expires_in: 1800 });
    }

    // Section 1: POST <base>/voice/v1/<service>. Bare /<service> is accepted too.
    const service = path.startsWith(PREFIX) ? path.slice(PREFIX.length) : path.slice(1);
    if (handlers[service]) {
      if (method !== "POST") {
        return json(errorBody("METHOD_NOT_ALLOWED", "ERROR", `Use POST for ${service}`, null), 405);
      }
      return handleService(service, request, env);
    }
    return json(errorBody("NOT_FOUND", "ERROR", `Unknown service: ${url.pathname}`, null), 404);
  },
};
