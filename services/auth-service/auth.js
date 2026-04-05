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
    fetch(LOG_URL, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    }).catch(() => {});
  }
}

// ── Realistic user pool ───────────────────────────────────────────────────────
const USERS = [
  { user: "alice@acme.com",   role: "admin" },
  { user: "bob@acme.com",     role: "engineer" },
  { user: "carol@acme.com",   role: "analyst" },
  { user: "dave@acme.com",    role: "engineer" },
  { user: "eve@acme.com",     role: "viewer" },
];
const SUSPICIOUS_IPS = ["203.0.113.42", "198.51.100.77"];
const CLIENT_IPS = ["10.0.1.12", "10.0.1.45", "10.0.2.8", "10.0.3.21"];

// Track consecutive failures per user for rate-limit simulation
const failCounts = {};

function randInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}
function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}
function sessionId() {
  return "sess-" + Math.random().toString(36).slice(2, 10);
}
function traceId() {
  return Math.random().toString(16).slice(2, 18);
}
function jwtId() {
  return "jwt-" + Math.random().toString(36).slice(2, 12);
}

// ── Middleware ────────────────────────────────────────────────────────────────
app.use((req, res, next) => {
  const end = httpDuration.startTimer();
  res.on("finish", () => {
    end({ method: req.method, route: req.route?.path || req.path, status: String(res.statusCode) });
  });
  next();
});

app.get("/health", (_req, res) => {
  log("INFO", "health check OK", { uptime_s: Math.floor(process.uptime()) });
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

// ── Background realistic event emitter ───────────────────────────────────────
function emitLoginSuccess() {
  const u = pick(USERS);
  const ip = pick(CLIENT_IPS);
  const latency = randInt(18, 120);
  const sid = sessionId();
  const token = jwtId();
  loginAttempts.inc({ result: "success" });
  log("INFO", `POST /login 200 OK -- user=${u.user} role=${u.role} latency=${latency}ms session=${sid}`, {
    user: u.user, role: u.role, ip, latency_ms: latency, session: sid, token, route: "/login",
  });
}

function emitLoginFailure() {
  const ip = pick([...CLIENT_IPS, ...SUSPICIOUS_IPS]);
  const fakeUser = pick(["root", "admin@test.com", "unknown@attacker.io", "test@test.com"]);
  failCounts[fakeUser] = (failCounts[fakeUser] || 0) + 1;
  if (failCounts[fakeUser] >= 3) {
    loginAttempts.inc({ result: "fail" });
    log("WARN", `POST /login 429 -- rate limit applied: ${failCounts[fakeUser]} failed attempts for user=${fakeUser} ip=${ip}`, {
      user: fakeUser, ip, fail_count: failCounts[fakeUser], status: 429,
    });
    failCounts[fakeUser] = 0;
  } else {
    loginAttempts.inc({ result: "fail" });
    log("WARN", `POST /login 401 Unauthorized -- invalid credentials for user=${fakeUser} ip=${ip} (attempt ${failCounts[fakeUser]}/3)`, {
      user: fakeUser, ip, fail_count: failCounts[fakeUser], status: 401,
    });
  }
}

function emitJwtRefresh() {
  const u = pick(USERS);
  const latency = randInt(8, 35);
  log("INFO", `POST /token/refresh 200 OK -- user=${u.user} new_token=${jwtId()} latency=${latency}ms`, {
    user: u.user, latency_ms: latency, route: "/token/refresh",
  });
}

function emitJwtExpiry() {
  const u = pick(USERS);
  log("WARN", `GET /api/profile 401 -- JWT expired for user=${u.user}, forcing re-authentication`, {
    user: u.user, reason: "jwt_expired", route: "/api/profile",
  });
}

function emitSuspiciousActivity() {
  const ip = pick(SUSPICIOUS_IPS);
  const count = randInt(12, 40);
  log("WARN", `Suspicious login burst detected -- ${count} attempts in 60s from ip=${ip}, blocking`, {
    ip, attempt_count: count, window_s: 60, action: "block",
  });
}

function emitMfaChallenge() {
  const u = pick(USERS);
  log("INFO", `MFA challenge issued -- user=${u.user} method=totp expires_in=300s`, {
    user: u.user, mfa_method: "totp", expires_s: 300,
  });
}

function emitSessionExpiry() {
  const u = pick(USERS);
  const sid = sessionId();
  log("INFO", `Session expired -- user=${u.user} session=${sid} idle_for=1800s`, {
    user: u.user, session: sid, idle_s: 1800, reason: "idle_timeout",
  });
}

// Weighted event scheduler
const AUTH_EVENTS = [
  { fn: emitLoginSuccess,      weight: 50 },
  { fn: emitLoginFailure,      weight: 15 },
  { fn: emitJwtRefresh,        weight: 15 },
  { fn: emitJwtExpiry,         weight:  7 },
  { fn: emitMfaChallenge,      weight:  5 },
  { fn: emitSessionExpiry,     weight:  5 },
  { fn: emitSuspiciousActivity,weight:  3 },
];
const TOTAL_WEIGHT = AUTH_EVENTS.reduce((s, e) => s + e.weight, 0);

function pickWeighted() {
  let r = Math.random() * TOTAL_WEIGHT;
  for (const e of AUTH_EVENTS) {
    r -= e.weight;
    if (r <= 0) return e.fn;
  }
  return AUTH_EVENTS[0].fn;
}

function startAuthSimulator() {
  log("INFO", "Auth service initialized -- JWT issuer=devops-ai ttl=3600s mfa=enabled", {
    jwt_issuer: "devops-ai", ttl_s: 3600, mfa: true,
  });

  (function loop() {
    pickWeighted()();
    setTimeout(loop, randInt(3000, 9000));
  })();
}

const port = Number(process.env.PORT || 8080);
app.listen(port, () => {
  log("INFO", `auth-service listening on :${port}`);
  startAuthSimulator();
});
