import express from "express";
import client from "prom-client";

const app = express();
app.use(express.json());

const register = new client.Registry();
client.collectDefaultMetrics({ register });

const httpDuration = new client.Histogram({
  name: "http_request_duration_ms",
  help: "HTTP request duration ms",
  labelNames: ["method", "route", "status"],
  buckets: [5, 10, 25, 50, 100, 250, 500, 1000],
  registers: [register],
});

const payments = new client.Counter({
  name: "payment_transactions_total",
  help: "Payment transactions",
  labelNames: ["status"],
  registers: [register],
});

const LOG_URL = process.env.LOG_INGEST_URL || "";
const INGEST_SECRET = process.env.INGEST_SECRET || "";
const SERVICE = "payment-service";
const FAIL_MODE = process.env.FAIL_MODE === "1";

function log(level, message, extra = {}) {
  const payload = {
    time: new Date().toISOString(),
    service: SERVICE,
    level,
    message,
    extra,
  };
  console.log(JSON.stringify(payload));
  if (LOG_URL) {
    const headers = { "Content-Type": "application/json" };
    if (INGEST_SECRET) headers["X-Ingest-Secret"] = INGEST_SECRET;
    fetch(LOG_URL, { method: "POST", headers, body: JSON.stringify(payload) }).catch(() => {});
  }
}

// ── Realistic data pools ──────────────────────────────────────────────────────
const MERCHANTS = ["stripe", "paypal", "adyen", "braintree"];
const CURRENCIES = ["USD", "EUR", "GBP", "INR"];
const USERS = ["alice@acme.com", "bob@acme.com", "carol@acme.com", "dave@acme.com", "eve@acme.com"];
const ERROR_TYPES = [
  { code: "CARD_DECLINED",        msg: "card declined by issuer (insufficient funds)",  status: 402 },
  { code: "FRAUD_CHECK_FAILED",   msg: "fraud risk score too high (score=87/100)",       status: 402 },
  { code: "DB_TIMEOUT",           msg: "DB query timed out after 5000ms",                status: 503 },
  { code: "UPSTREAM_TIMEOUT",     msg: "upstream gateway timeout after 10000ms",         status: 503 },
  { code: "INVALID_CARD",         msg: "card validation failed: CVV mismatch",           status: 400 },
];

function randInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
function pick(arr)          { return arr[Math.floor(Math.random() * arr.length)]; }
function txnId()            { return "txn-" + randInt(10000, 99999); }
function traceId()          { return Math.random().toString(16).slice(2, 18); }

// Circuit-breaker state
let cbState   = "CLOSED"; // CLOSED | HALF_OPEN | OPEN
let cbFailures = 0;
const CB_THRESHOLD = 5;

function updateCB(success) {
  if (success) {
    if (cbState === "HALF_OPEN") {
      cbState = "CLOSED";
      cbFailures = 0;
      log("INFO", "Circuit breaker CLOSED -- upstream healthy, resuming normal traffic", {
        circuit_breaker: "CLOSED",
      });
    } else {
      cbFailures = Math.max(0, cbFailures - 1);
    }
  } else {
    cbFailures++;
    if (cbState === "CLOSED" && cbFailures >= CB_THRESHOLD) {
      cbState = "OPEN";
      log("ERROR", `Circuit breaker tripped OPEN -- ${cbFailures} consecutive failures, fast-failing for 30s`, {
        circuit_breaker: "OPEN", failures: cbFailures, fast_fail_s: 30,
      });
      // Auto half-open after 30s
      setTimeout(() => {
        cbState = "HALF_OPEN";
        log("WARN", "Circuit breaker HALF-OPEN -- probing upstream with limited traffic", {
          circuit_breaker: "HALF_OPEN",
        });
      }, 30000);
    }
  }
}

// ── Express routes ────────────────────────────────────────────────────────────
app.use((req, res, next) => {
  const end = httpDuration.startTimer();
  res.on("finish", () => {
    end({ method: req.method, route: req.route?.path || req.path, status: String(res.statusCode) });
  });
  next();
});

app.get("/health", (_req, res) => {
  log("INFO", "health check OK", { circuit_breaker: cbState, uptime_s: Math.floor(process.uptime()) });
  res.json({ status: "ok", service: SERVICE, circuit_breaker: cbState });
});

app.post("/pay", (req, res) => {
  if (FAIL_MODE) {
    payments.inc({ status: "error" });
    log("ERROR", "payment failed: simulated outage (FAIL_MODE=1)", { amount: req.body?.amount });
    return res.status(503).json({ error: "service unavailable" });
  }
  payments.inc({ status: "ok" });
  log("INFO", "payment processed", { amount: req.body?.amount });
  res.json({ ok: true, id: txnId() });
});

app.get("/metrics", async (_req, res) => {
  res.set("Content-Type", register.contentType);
  res.send(await register.metrics());
});

// ── Background realistic event emitter ───────────────────────────────────────
function emitSuccessfulPayment() {
  const id       = txnId();
  const amount   = (randInt(5, 999) + randInt(0, 99) / 100).toFixed(2);
  const currency = pick(CURRENCIES);
  const merchant = pick(MERCHANTS);
  const user     = pick(USERS);
  const latency  = randInt(45, 350);
  const dbMs     = randInt(8, 60);
  const trace    = traceId();
  payments.inc({ status: "ok" });
  log("INFO", `POST /pay 200 OK -- txn=${id} amount=${currency} ${amount} merchant=${merchant} user=${user} latency=${latency}ms`, {
    txn_id: id, amount: parseFloat(amount), currency, merchant, user, latency_ms: latency, db_query_ms: dbMs, trace,
  });
}

function emitFailedPayment() {
  const err    = pick(ERROR_TYPES);
  const id     = txnId();
  const amount = (randInt(5, 999) + randInt(0, 99) / 100).toFixed(2);
  const user   = pick(USERS);
  const trace  = traceId();
  payments.inc({ status: "error" });
  log("ERROR", `POST /pay ${err.status} -- txn=${id} ${err.msg} user=${user}`, {
    txn_id: id, amount: parseFloat(amount), user, error_code: err.code, status: err.status, trace,
  });
  updateCB(false);
}

function emitSlowQuery() {
  const tables  = ["transactions", "payments", "audit_log", "users", "refunds"];
  const table   = pick(tables);
  const queryMs = randInt(800, 3500);
  log("WARN", `Slow DB query detected -- SELECT FROM ${table} duration=${queryMs}ms (threshold=200ms) consider adding index`, {
    table, query_ms: queryMs, threshold_ms: 200, action: "index_recommended",
  });
}

function emitPoolPressure() {
  const active  = randInt(14, 19);
  const waiting = randInt(1, 8);
  log("WARN", `DB connection pool pressure -- active=${active}/20 waiting=${waiting} -- consider scaling`, {
    active_conn: active, pool_size: 20, waiting, threshold_pct: Math.round(active / 20 * 100),
  });
}

function emitRefund() {
  const id      = txnId();
  const origId  = txnId();
  const amount  = (randInt(5, 200) + randInt(0, 99) / 100).toFixed(2);
  const user    = pick(USERS);
  payments.inc({ status: "refund" });
  log("INFO", `POST /refund 200 OK -- refund=${id} original_txn=${origId} amount=USD ${amount} user=${user}`, {
    refund_id: id, original_txn: origId, amount: parseFloat(amount), user,
  });
}

function emitFraudAlert() {
  const user  = pick(USERS);
  const score = randInt(78, 96);
  const id    = txnId();
  log("WARN", `Fraud check flagged -- txn=${id} user=${user} risk_score=${score}/100 (threshold=75) -- holding for review`, {
    txn_id: id, user, risk_score: score, threshold: 75, action: "hold_for_review",
  });
}

function emitHighLatencyAlert() {
  const p99 = randInt(1100, 2800);
  log("WARN", `POST /pay latency spike -- p99=${p99}ms exceeds SLA threshold of 1000ms`, {
    p99_ms: p99, sla_ms: 1000, action: "alert_triggered",
  });
}

function emitCBOpen() {
  if (cbState !== "OPEN") {
    cbFailures = CB_THRESHOLD;
    updateCB(false);
  }
}

// Weighted schedule
const PAY_EVENTS = [
  { fn: emitSuccessfulPayment, weight: 55 },
  { fn: emitFailedPayment,     weight: 12 },
  { fn: emitSlowQuery,         weight: 10 },
  { fn: emitRefund,            weight:  8 },
  { fn: emitPoolPressure,      weight:  6 },
  { fn: emitFraudAlert,        weight:  5 },
  { fn: emitHighLatencyAlert,  weight:  4 },
];
const TOTAL_WEIGHT = PAY_EVENTS.reduce((s, e) => s + e.weight, 0);

function pickWeighted() {
  let r = Math.random() * TOTAL_WEIGHT;
  for (const e of PAY_EVENTS) {
    r -= e.weight;
    if (r <= 0) return e.fn;
  }
  return PAY_EVENTS[0].fn;
}

function startPaymentSimulator() {
  log("INFO", "Payment service started -- DB pool initialized connections=5/20 circuit_breaker=CLOSED", {
    pool_size: 20, initial_connections: 5, circuit_breaker: "CLOSED",
  });

  // Occasional circuit-breaker trip for drama (every ~5-8 min)
  function scheduleCBDrama() {
    setTimeout(() => {
      if (cbState === "CLOSED") {
        log("WARN", "DB response degrading -- slow queries accumulating, approaching failure threshold", {
          warning: "pre_failure", consecutive_slow: randInt(3, 5),
        });
        setTimeout(emitCBOpen, randInt(8000, 15000));
      }
      scheduleCBDrama();
    }, randInt(300000, 480000));
  }
  scheduleCBDrama();

  (function loop() {
    if (cbState === "OPEN") {
      log("WARN", "POST /pay fast-failed -- circuit breaker is OPEN, not forwarding to upstream", {
        circuit_breaker: "OPEN",
      });
    } else {
      pickWeighted()();
      if (cbState === "HALF_OPEN") updateCB(true);
    }
    setTimeout(loop, randInt(4000, 10000));
  })();
}

const port = Number(process.env.PORT || 8080);
app.listen(port, () => {
  log("INFO", `payment-service listening on :${port}`);
  startPaymentSimulator();
});
