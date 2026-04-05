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
const INGEST_SECRET = process.env.INGEST_SECRET || "";
const SERVICE = "frontend-service";

const AUTH_URL = process.env.AUTH_URL || "http://auth-service:8080";
const PAY_URL  = process.env.PAY_URL  || "http://payment-service:8080";

function log(level, message, extra = {}) {
  const payload = { time: new Date().toISOString(), service: SERVICE, level, message, extra };
  console.log(JSON.stringify(payload));
  if (LOG_URL) {
    const headers = { "Content-Type": "application/json" };
    if (INGEST_SECRET) headers["X-Ingest-Secret"] = INGEST_SECRET;
    fetch(LOG_URL, { method: "POST", headers, body: JSON.stringify(payload) }).catch(() => {});
  }
}

// ── Data pools ────────────────────────────────────────────────────────────────
const USERS    = ["alice@acme.com", "bob@acme.com", "carol@acme.com", "dave@acme.com", "eve@acme.com"];
const PAGES    = ["/dashboard", "/orders", "/cart", "/checkout", "/profile", "/settings", "/reports"];
const BROWSERS = ["Chrome/123", "Firefox/125", "Safari/17", "Edge/122"];
const DEVICES  = ["desktop", "mobile", "tablet"];

function randInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
function pick(arr)          { return arr[Math.floor(Math.random() * arr.length)]; }
function traceId()          { return Math.random().toString(16).slice(2, 18); }
function sessionId()        { return "sess-" + Math.random().toString(36).slice(2, 10); }

// ── Express routes ────────────────────────────────────────────────────────────
app.use((req, res, next) => {
  const end = httpDuration.startTimer();
  res.on("finish", () => {
    end({ method: req.method, route: req.route?.path || req.path, status: String(res.statusCode) });
  });
  next();
});

app.use(express.static(path.join(__dirname, "public")));

app.get("/health", (_req, res) => {
  log("INFO", "health check OK", { uptime_s: Math.floor(process.uptime()) });
  res.json({ status: "ok", service: SERVICE });
});

app.get("/metrics", async (_req, res) => {
  res.set("Content-Type", register.contentType);
  res.send(await register.metrics());
});

// ── Traffic simulator ─────────────────────────────────────────────────────────

// Page view simulation (lightweight, no upstream call)
function emitPageView() {
  const user    = pick(USERS);
  const page    = pick(PAGES);
  const latency = randInt(80, 1200);
  const browser = pick(BROWSERS);
  const device  = pick(DEVICES);
  const trace   = traceId();
  const level   = latency > 900 ? "WARN" : "INFO";
  const sfx     = latency > 900 ? ` -- SLOW (threshold=900ms)` : "";
  log(level, `GET ${page} 200 OK -- user=${user} latency=${latency}ms device=${device}${sfx}`, {
    user, page, latency_ms: latency, browser, device, trace,
  });
}

function emitSlowPage() {
  const user    = pick(USERS);
  const page    = pick(["/checkout", "/reports", "/dashboard"]);
  const latency = randInt(2100, 5800);
  const trace   = traceId();
  log("WARN", `GET ${page} -- page render exceeded SLA: ${latency}ms (threshold=2000ms) user=${user}`, {
    user, page, latency_ms: latency, threshold_ms: 2000, trace,
  });
}

function emitNotFound() {
  const paths = ["/api/v1/products/9999", "/favicon.ico", "/robots.txt", "/admin", "/wp-admin"];
  const p     = pick(paths);
  const user  = Math.random() > 0.5 ? pick(USERS) : "anonymous";
  log("WARN", `GET ${p} 404 Not Found -- user=${user}`, {
    path: p, user, status: 404,
  });
}

function emitCsrfError() {
  const user = pick(USERS);
  log("WARN", `POST /checkout 403 Forbidden -- CSRF token mismatch for user=${user}, rejecting`, {
    user, reason: "csrf_mismatch", status: 403,
  });
}

// Login journey: calls auth upstream
async function emitLoginJourney() {
  const user    = pick(USERS);
  const sid     = sessionId();
  const trace   = traceId();
  const succeed = Math.random() > 0.08; // 8% failure

  try {
    const body  = succeed
      ? { user: "demo", password: "demo" }
      : { user, password: "wrongpass" };
    const r     = await fetch(`${AUTH_URL}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const latency = randInt(30, 180);
    if (r.ok) {
      log("INFO", `Login flow complete -- user=${user} session=${sid} latency=${latency}ms`, {
        user, session: sid, latency_ms: latency, trace, status: 200,
      });
    } else {
      log("WARN", `Login flow failed -- user=${user} upstream_status=${r.status} redirecting to /login`, {
        user, upstream_status: r.status, trace, session: sid,
      });
    }
  } catch (e) {
    log("ERROR", `Login flow error -- auth-service unreachable: ${e.message} user=${user}`, {
      user, error: e.message, trace,
    });
  }
}

// Checkout journey: calls payment upstream
async function emitCheckoutJourney() {
  const user    = pick(USERS);
  const amount  = (randInt(5, 999) + randInt(0, 99) / 100).toFixed(2);
  const trace   = traceId();
  const sid     = sessionId();

  log("INFO", `Checkout initiated -- user=${user} cart_total=USD ${amount} session=${sid}`, {
    user, amount: parseFloat(amount), session: sid, trace,
  });

  try {
    const r       = await fetch(`${PAY_URL}/pay`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: parseFloat(amount) }),
    });
    const latency = randInt(100, 600);

    if (r.ok) {
      log("INFO", `Checkout complete -- user=${user} amount=USD ${amount} latency=${latency}ms session=${sid}`, {
        user, amount: parseFloat(amount), latency_ms: latency, session: sid, trace, status: 200,
      });
    } else {
      const status = r.status;
      const isServerErr = status >= 500;
      log(isServerErr ? "ERROR" : "WARN",
        `Checkout failed -- user=${user} amount=USD ${amount} upstream_status=${status} session=${sid}`,
        { user, amount: parseFloat(amount), upstream_status: status, session: sid, trace }
      );

      // Retry on 503
      if (status === 503) {
        log("WARN", `Checkout retry #1 -- user=${user} retrying after 1s due to upstream 503`, {
          user, retry: 1, session: sid, trace,
        });
        await new Promise(r => setTimeout(r, 1000));
        const r2 = await fetch(`${PAY_URL}/pay`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ amount: parseFloat(amount) }),
        });
        if (!r2.ok) {
          log("ERROR", `Checkout retry #1 failed -- user=${user} upstream still ${r2.status}, giving up`, {
            user, upstream_status: r2.status, session: sid, trace, retries_exhausted: true,
          });
        } else {
          log("INFO", `Checkout retry #1 succeeded -- user=${user} amount=USD ${amount}`, {
            user, amount: parseFloat(amount), session: sid, trace,
          });
        }
      }
    }
  } catch (e) {
    log("ERROR", `Checkout error -- payment-service unreachable: ${e.message} user=${user}`, {
      user, error: e.message, session: sid, trace,
    });
  }
}

function emitCacheHitMiss() {
  const hitRatio = (Math.random() * 0.35 + 0.62).toFixed(3); // 0.62–0.97
  const level    = hitRatio < 0.75 ? "WARN" : "INFO";
  const sfx      = hitRatio < 0.75 ? " -- below threshold, increasing origin load" : "";
  log(level, `CDN cache stats -- hit_ratio=${hitRatio} requests_last_60s=${randInt(80, 400)}${sfx}`, {
    hit_ratio: parseFloat(hitRatio), window_s: 60,
  });
}

function emitWebVital() {
  const lcp = randInt(800, 4200);
  const fid = randInt(10, 180);
  const cls = (Math.random() * 0.3).toFixed(3);
  const user = pick(USERS);
  const page = pick(PAGES);
  const lvl  = lcp > 2500 ? "WARN" : "INFO";
  log(lvl, `Web Vitals -- user=${user} page=${page} LCP=${lcp}ms FID=${fid}ms CLS=${cls}${lcp > 2500 ? " -- POOR LCP" : ""}`, {
    user, page, lcp_ms: lcp, fid_ms: fid, cls: parseFloat(cls),
  });
}

// Weighted event table
const FE_EVENTS = [
  { fn: emitPageView,        weight: 40 },
  { fn: emitLoginJourney,    weight: 18 },
  { fn: emitCheckoutJourney, weight: 15 },
  { fn: emitWebVital,        weight: 10 },
  { fn: emitCacheHitMiss,    weight:  7 },
  { fn: emitSlowPage,        weight:  5 },
  { fn: emitNotFound,        weight:  3 },
  { fn: emitCsrfError,       weight:  2 },
];
const TOTAL_WEIGHT = FE_EVENTS.reduce((s, e) => s + e.weight, 0);

function pickWeighted() {
  let r = Math.random() * TOTAL_WEIGHT;
  for (const e of FE_EVENTS) {
    r -= e.weight;
    if (r <= 0) return e.fn;
  }
  return FE_EVENTS[0].fn;
}

function startTrafficSimulator() {
  log("INFO", "Frontend service started -- CDN=enabled SSR=false analytics=true", {
    cdn: true, ssr: false, analytics: true,
  });
  log("INFO", "traffic simulator started -- generating realistic user journeys", {
    users: USERS.length, events: FE_EVENTS.map(e => e.fn.name),
  });

  (function loop() {
    pickWeighted()();
    setTimeout(loop, randInt(3000, 8000));
  })();
}

const port = Number(process.env.PORT || 8080);
app.listen(port, () => {
  log("INFO", `frontend-service listening on :${port}`);
  startTrafficSimulator();
});
