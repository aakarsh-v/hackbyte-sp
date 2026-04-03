import express from "express";
import path from "path";
import { fileURLToPath } from "url";
import client from "prom-client";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();

const register = new client.Registry();
client.collectDefaultMetrics({ register });

const httpDuration = new client.Histogram({
  name: "http_request_duration_ms",
  help: "HTTP request duration ms",
  labelNames: ["method", "route", "status"],
  buckets: [5, 10, 25, 50, 100, 250, 500, 1000],
  registers: [register],
});

const LOG_URL = process.env.LOG_INGEST_URL || "";
const SERVICE = "frontend-service";

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

app.use(express.static(path.join(__dirname, "public")));

app.get("/health", (_req, res) => {
  log("INFO", "health check");
  res.json({ status: "ok", service: SERVICE });
});

app.get("/metrics", async (_req, res) => {
  res.set("Content-Type", register.contentType);
  res.send(await register.metrics());
});

const port = Number(process.env.PORT || 8080);
app.listen(port, () => {
  log("INFO", `listening on ${port}`);
});
