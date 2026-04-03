import { useCallback, useEffect, useState } from "react";

const apiBase = import.meta.env.VITE_API_URL || "";

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
  const [logs, setLogs] = useState<string[]>([]);
  const [incident, setIncident] = useState(
    "PaymentService returns 503; CPU high on payment-service."
  );
  const [metricsHint, setMetricsHint] = useState("");
  const [analysis, setAnalysis] = useState("");
  const [rawRunbook, setRawRunbook] = useState("");
  const [sanitized, setSanitized] = useState("");
  const [blocked, setBlocked] = useState<PolicyViolation[]>([]);
  const [approvedHash, setApprovedHash] = useState<string | null>(null);
  const [execOut, setExecOut] = useState("");
  const [loading, setLoading] = useState(false);

  const appendLog = useCallback((line: string) => {
    setLogs((prev) => [...prev.slice(-400), line]);
  }, []);

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

  const runAnalyze = async () => {
    setLoading(true);
    setExecOut("");
    try {
      const r = await fetch(`${apiBase}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
      setApprovedHash(data.approved_hash);
    } catch (e) {
      setAnalysis(String(e));
    } finally {
      setLoading(false);
    }
  };

  const runExecute = async () => {
    if (!approvedHash || !sanitized) return;
    setLoading(true);
    setExecOut("");
    try {
      const r = await fetch(`${apiBase}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: sanitized,
          content_hash: approvedHash,
        }),
      });
      const text = await r.text();
      try {
        setExecOut(JSON.stringify(JSON.parse(text), null, 2));
      } catch {
        setExecOut(text);
      }
    } catch (e) {
      setExecOut(String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="layout">
      <h1>DevOps AI — console</h1>
      <p style={{ color: "var(--muted)", fontSize: "0.9rem", marginTop: 0 }}>
        Live logs (WebSocket) · Gemini analysis · policy · execute approved runbook
      </p>

      <div className="grid">
        <div className="panel">
          <label>Live log stream</label>
          <pre className="log">{logs.join("\n") || "…"}</pre>
        </div>

        <div className="panel">
          <label>Incident description</label>
          <textarea value={incident} onChange={(e) => setIncident(e.target.value)} />
          <label style={{ marginTop: "0.75rem" }}>Metrics / notes (optional)</label>
          <textarea value={metricsHint} onChange={(e) => setMetricsHint(e.target.value)} />
          <div className="actions">
            <button type="button" disabled={loading} onClick={runAnalyze}>
              Analyze + runbook (Gemini)
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
        <label>Policy — blocked lines</label>
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
        <label style={{ marginTop: "0.75rem" }}>Sanitized runbook (execute uses this)</label>
        <textarea value={sanitized} readOnly />
        <div className="actions">
          <button
            type="button"
            className="danger"
            disabled={loading || !approvedHash}
            onClick={runExecute}
          >
            Execute approved runbook
          </button>
          <span className="badge">hash: {approvedHash?.slice(0, 12) || "—"}…</span>
        </div>
      </div>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <label>Execution output</label>
        <pre className="log">{execOut || "—"}</pre>
      </div>
    </div>
  );
}
