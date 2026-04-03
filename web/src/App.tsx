import { useCallback, useEffect, useRef, useState } from "react";

const apiBase = import.meta.env.VITE_API_URL || "";

const SESSION_STORAGE_KEY = "devopsai_session_id";

function getOrCreateSessionId(): string {
  try {
    let id = localStorage.getItem(SESSION_STORAGE_KEY);
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem(SESSION_STORAGE_KEY, id);
    }
    return id;
  } catch {
    return "00000000-0000-0000-0000-000000000001";
  }
}

type LogEvent = {
  time: string;
  service: string;
  level: string;
  message: string;
};

type PolicyViolation = { line_number: number; line: string; reason: string };

type AnalyzeResp = {
  analysis: string;
  raw_runbook: string;
  preview: {
    original_lines: string[];
    sanitized_lines: string[];
    blocked: PolicyViolation[];
  };
  approved_hash: string | null;
};

type PolicyPreviewResp = {
  original_lines: string[];
  sanitized_lines: string[];
  blocked: PolicyViolation[];
};

function wsLogsUrl() {
  if (apiBase) {
    const u = new URL(apiBase);
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    u.pathname = "/ws/logs";
    u.search = "";
    return u.toString();
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/logs`;
}

export function App() {
  const [sessionId] = useState(() => getOrCreateSessionId());
  const [logs, setLogs] = useState<string[]>([]);
  const [incident, setIncident] = useState(
    "PaymentService returns 503; CPU high on payment-service."
  );
  const [metricsHint, setMetricsHint] = useState("");
  const [analysis, setAnalysis] = useState("");
  const [rawRunbook, setRawRunbook] = useState("");
  const [sanitized, setSanitized] = useState("");
  const [sanitizedHash, setSanitizedHash] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<PolicyViolation[]>([]);
  const [approvedHash, setApprovedHash] = useState<string | null>(null);
  const [execOut, setExecOut] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [approving, setApproving] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [revalidating, setRevalidating] = useState(false);
  const revalidateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const logBoxRef = useRef<HTMLPreElement>(null);

  const appendLog = useCallback((line: string) => {
    setLogs((prev) => [...prev.slice(-400), line]);
  }, []);

  // Auto-scroll log panel
  useEffect(() => {
    if (logBoxRef.current) {
      logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight;
    }
  }, [logs]);

  useEffect(() => {
    const ws = new WebSocket(wsLogsUrl());
    ws.onmessage = (ev) => {
      try {
        const e: LogEvent = JSON.parse(ev.data as string);
        appendLog(`${e.time} [${e.service}] ${e.level}: ${e.message}`);
      } catch {
        appendLog(String(ev.data));
      }
    };
    ws.onerror = () => appendLog("[ws] connection error");
    return () => ws.close();
  }, [appendLog]);

  // ----- Analyze -----
  const runAnalyze = async () => {
    setLoading(true);
    setExecOut([]);
    setApprovedHash(null);
    try {
      const r = await fetch(`${apiBase}/analyze`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Session-Id": sessionId,
        },
        body: JSON.stringify({
          incident_description: incident,
          include_logs: true,
          include_metrics_hint: metricsHint,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      const data: AnalyzeResp = await r.json();
      setAnalysis(data.analysis);
      setRawRunbook(data.raw_runbook);
      setBlocked(data.preview.blocked);
      const san = data.preview.sanitized_lines.join("\n");
      setSanitized(san);
      setSanitizedHash(data.approved_hash);
      setApprovedHash(null); // must explicitly approve
    } catch (e) {
      setAnalysis(String(e));
    } finally {
      setLoading(false);
    }
  };

  // ----- Re-validate runbook when user edits it -----
  const handleSanitizedChange = (value: string) => {
    setSanitized(value);
    setApprovedHash(null); // editing invalidates approval
    if (revalidateTimer.current) clearTimeout(revalidateTimer.current);
    revalidateTimer.current = setTimeout(async () => {
      setRevalidating(true);
      try {
        const r = await fetch(`${apiBase}/policy/preview`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ script: value }),
        });
        if (!r.ok) return;
        const data: PolicyPreviewResp = await r.json();
        setBlocked(data.blocked);
        // Compute new hash of valid sanitized content
        const san = data.sanitized_lines.join("\n");
        const hashBuf = await crypto.subtle.digest(
          "SHA-256",
          new TextEncoder().encode(san)
        );
        const newHash = Array.from(new Uint8Array(hashBuf))
          .map((b) => b.toString(16).padStart(2, "0"))
          .join("");
        setSanitizedHash(newHash);
      } finally {
        setRevalidating(false);
      }
    }, 500);
  };

  // ----- Approve (mandatory gate) -----
  const runApprove = async () => {
    if (!sanitized || !sanitizedHash) return;
    setApproving(true);
    try {
      const r = await fetch(`${apiBase}/approve`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Session-Id": sessionId,
        },
        body: JSON.stringify({
          content: sanitized,
          content_hash: sanitizedHash,
        }),
      });
      if (!r.ok) {
        const msg = await r.text();
        setExecOut([`Approval failed: ${msg}`]);
        return;
      }
      const data = await r.json();
      setApprovedHash(data.hash);
      setExecOut(["✅ Runbook approved. You may now execute."]);
    } catch (e) {
      setExecOut([String(e)]);
    } finally {
      setApproving(false);
    }
  };

  // ----- Execute with SSE streaming -----
  const runExecute = async () => {
    if (!approvedHash || !sanitized) return;
    setStreaming(true);
    setExecOut([]);
    try {
      const r = await fetch(`${apiBase}/execute/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Session-Id": sessionId,
        },
        body: JSON.stringify({
          content: sanitized,
          content_hash: approvedHash,
        }),
      });
      if (!r.ok) {
        const msg = await r.text();
        setExecOut([`Execute error: ${msg}`]);
        return;
      }
      const reader = r.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const evt = JSON.parse(line.slice(6));
              if (evt.type === "output") {
                setExecOut((prev) => [...prev, evt.data]);
              } else if (evt.type === "done") {
                setExecOut((prev) => [...prev, "✅ Execution complete."]);
              } else if (evt.type === "error") {
                setExecOut((prev) => [...prev, `❌ ${evt.data}`]);
              }
            } catch {}
          }
        }
      }
    } catch (e) {
      setExecOut([String(e)]);
    } finally {
      setStreaming(false);
    }
  };

  const isApproved = !!approvedHash;
  const hasSanitized = sanitized.trim().length > 0;
  const execBusy = loading || streaming || approving;

  return (
    <div className="layout">
      <h1>DevOps AI — console</h1>
      <p style={{ color: "var(--muted)", fontSize: "0.9rem", marginTop: 0 }}>
        Live logs · Gemini analysis · VeriGuard policy · approve → execute
      </p>

      <div className="grid">
        <div className="panel">
          <label>Live log stream</label>
          <pre className="log" ref={logBoxRef}>
            {logs.join("\n") || "…"}
          </pre>
        </div>

        <div className="panel">
          <label>Incident description</label>
          <textarea value={incident} onChange={(e) => setIncident(e.target.value)} />
          <label style={{ marginTop: "0.75rem" }}>Metrics / notes (optional)</label>
          <textarea value={metricsHint} onChange={(e) => setMetricsHint(e.target.value)} />
          <div className="actions">
            <button type="button" disabled={execBusy} onClick={runAnalyze}>
              {loading ? "Analyzing…" : "Analyze + runbook (Gemini)"}
            </button>
          </div>
        </div>
      </div>

      <div className="grid" style={{ marginTop: "1rem" }}>
        <div className="panel">
          <label>Analysis</label>
          <pre className="log">{analysis || "—"}</pre>
        </div>
        <div className="panel">
          <label>Raw runbook</label>
          <pre className="log">{rawRunbook || "—"}</pre>
        </div>
      </div>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <label>
          Policy — blocked lines
          {revalidating && (
            <span style={{ marginLeft: "0.5rem", color: "var(--muted)", fontWeight: 400 }}>
              (re-validating…)
            </span>
          )}
        </label>
        {blocked.length === 0 ? (
          <p className="ok">No blocked lines in preview.</p>
        ) : (
          <ul style={{ margin: 0, paddingLeft: "1.2rem" }}>
            {blocked.map((b) => (
              <li key={b.line_number} className="blocked">
                <span className="badge">L{b.line_number}</span>
                {b.reason}: <code>{b.line}</code>
              </li>
            ))}
          </ul>
        )}

        <label style={{ marginTop: "0.75rem" }}>
          Sanitized runbook{" "}
          <span style={{ color: "var(--muted)", fontWeight: 400 }}>
            (editable — changes re-validate policy and require re-approval)
          </span>
        </label>
        <textarea
          value={sanitized}
          onChange={(e) => handleSanitizedChange(e.target.value)}
          placeholder="Run Analyze first…"
        />

        <div className="actions" style={{ gap: "0.75rem", flexWrap: "wrap" }}>
          {/* Step 1: Approve */}
          <button
            type="button"
            disabled={execBusy || !hasSanitized || isApproved}
            onClick={runApprove}
            style={{ background: isApproved ? "var(--ok, #2a7a2a)" : undefined }}
          >
            {approving
              ? "Approving…"
              : isApproved
              ? "✅ Approved"
              : "Approve runbook"}
          </button>

          {/* Step 2: Execute (locked until approved) */}
          <button
            type="button"
            className="danger"
            disabled={execBusy || !isApproved}
            onClick={runExecute}
            title={!isApproved ? "Approve the runbook first" : ""}
          >
            {streaming ? "Executing…" : "Execute approved runbook"}
          </button>

          <span className="badge">
            hash:{" "}
            {(approvedHash ?? sanitizedHash)?.slice(0, 12) || "—"}…
            {isApproved ? " ✅" : " (not approved)"}
          </span>
        </div>
      </div>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <label>Execution output {streaming && <span style={{ color: "var(--muted)" }}>(streaming…)</span>}</label>
        <pre className="log">
          {execOut.length > 0 ? execOut.join("\n") : "—"}
        </pre>
      </div>
    </div>
  );
}
