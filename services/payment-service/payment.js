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
    fetch(LOG_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(() => {});
  }
}

app.use((req, res, next) => {
  const end = httpDuration.startTimer();
  res.on("finish", () => {
    end({
      method: req.method,
      route: req.route?.path || req.path,
      status: String(res.statusCode),
    });
  });
  next();
});

app.get("/health", (_req, res) => {
  log("INFO", "health check");
  res.json({ status: "ok", service: SERVICE });
});

app.post("/pay", (req, res) => {
  if (FAIL_MODE) {
    payments.inc({ status: "error" });
    log("ERROR", "payment failed: simulated outage", { amount: req.body?.amount });
    return res.status(503).json({ error: "service unavailable" });
  }
  payments.inc({ status: "ok" });
  log("INFO", "payment processed", { amount: req.body?.amount });
  res.json({ ok: true, id: "txn-" + Date.now() });
});

app.get("/metrics", async (_req, res) => {
  res.set("Content-Type", register.contentType);
  res.send(await register.metrics());
});

const port = Number(process.env.PORT || 8080);
app.listen(port, () => {
  log("INFO", `listening on ${port}`);
});
