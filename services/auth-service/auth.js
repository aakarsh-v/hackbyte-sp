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

const loginAttempts = new client.Counter({
  name: "auth_login_attempts_total",
  help: "Login attempts",
  labelNames: ["result"],
  registers: [register],
});

const LOG_URL = process.env.LOG_INGEST_URL || "";
const INGEST_SECRET = process.env.INGEST_SECRET || "";
const SERVICE = "auth-service";

function log(level, message, extra = {}) {
  const line = JSON.stringify({
    time: new Date().toISOString(),
    service: SERVICE,
    level,
    message,
    ...extra,
  });
  console.log(line);
  if (LOG_URL) {
    const headers = { "Content-Type": "application/json" };
    if (INGEST_SECRET) headers["X-Ingest-Secret"] = INGEST_SECRET;
    fetch(LOG_URL, {
      method: "POST",
      headers,
      body: JSON.stringify({
        time: new Date().toISOString(),
        service: SERVICE,
        level,
        message,
        extra,
      }),
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

app.post("/login", (req, res) => {
  const ok = req.body?.user === "demo" && req.body?.password === "demo";
  loginAttempts.inc({ result: ok ? "success" : "fail" });
  if (ok) {
    log("INFO", "login succeeded", { user: req.body?.user });
    return res.json({ token: "stub-token" });
  }
  log("WARN", "login failed", { user: req.body?.user });
  res.status(401).json({ error: "invalid credentials" });
});

app.get("/metrics", async (_req, res) => {
  res.set("Content-Type", register.contentType);
  res.send(await register.metrics());
});

const port = Number(process.env.PORT || 8080);
app.listen(port, () => {
  log("INFO", `listening on ${port}`);
});
