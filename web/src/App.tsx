import { useCallback, useEffect, useRef, useState } from "react";
import { Plane, Activity, FileJson, Play, ShieldAlert, Cpu, Terminal, CheckCircle2, Database } from "lucide-react";

// @ts-ignore
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

type LogEvent = { time: string; service: string; level: string; message: string; };
type PolicyViolation = { line_number: number; line: string; reason: string };
type AnalyzeResp = { analysis: string; raw_runbook: string; preview: { original_lines: string[]; sanitized_lines: string[]; blocked: PolicyViolation[]; }; approved_hash: string | null; };
type PolicyPreviewResp = { original_lines: string[]; sanitized_lines: string[]; blocked: PolicyViolation[]; };

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
  const [activeTab, setActiveTab] = useState<"sandbox" | "grafana" | "prometheus" | "api">("sandbox");
  
  // SuperPlane State
  const [sessionId] = useState(() => getOrCreateSessionId());
  const [logs, setLogs] = useState<string[]>([]);
  const [incident, setIncident] = useState("EC2 production server critical failure: Nginx 502 Bad Gateway, payment-service OOM killed (exit code 137), database connection refused on db:5432. Multiple upstream retries exhausted.");
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
  const termBoxRef = useRef<HTMLPreElement>(null);

  const appendLog = useCallback((line: string) => {
    setLogs((prev) => [...prev.slice(-400), line]);
  }, []);

  useEffect(() => {
    if (logBoxRef.current) logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight;
  }, [logs, activeTab]);

  useEffect(() => {
    if (termBoxRef.current) termBoxRef.current.scrollTop = termBoxRef.current.scrollHeight;
  }, [execOut, activeTab]);

  useEffect(() => {
    const ws = new WebSocket(wsLogsUrl());
    ws.onmessage = (ev) => {
      try {
        const e: LogEvent = JSON.parse(ev.data as string);
        appendLog(`[${e.service}] ${e.level}: ${e.message}`);
      } catch {
        appendLog(String(ev.data));
      }
    };
    ws.onerror = () => appendLog("[system] connection error");
    return () => ws.close();
  }, [appendLog]);

  const runAnalyze = async () => {
    setLoading(true); setExecOut([]); setApprovedHash(null);
    try {
      const r = await fetch(`${apiBase}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Session-Id": sessionId },
        body: JSON.stringify({ incident_description: incident, include_logs: true, include_metrics_hint: metricsHint }),
      });
      if (!r.ok) throw new Error(await r.text());
      const data: AnalyzeResp = await r.json();
      setAnalysis(data.analysis);
      setRawRunbook(data.raw_runbook);
      setBlocked(data.preview.blocked);
      setSanitized(data.preview.sanitized_lines.join("\n"));
      setSanitizedHash(data.approved_hash);
      setApprovedHash(null);
    } catch (e) {
      setAnalysis(String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleSanitizedChange = (value: string) => {
    setSanitized(value);
    setApprovedHash(null);
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
        const san = data.sanitized_lines.join("\n");
        const hashBuf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(san));
        const newHash = Array.from(new Uint8Array(hashBuf)).map((b) => b.toString(16).padStart(2, "0")).join("");
        setSanitizedHash(newHash);
      } finally {
        setRevalidating(false);
      }
    }, 500);
  };

  const runApprove = async () => {
    if (!sanitized || !sanitizedHash) return;
    setApproving(true);
    try {
      const r = await fetch(`${apiBase}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Session-Id": sessionId },
        body: JSON.stringify({ content: sanitized, content_hash: sanitizedHash }),
      });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      setApprovedHash(data.hash);
      setExecOut(["✅ Runbook computationally approved matching Hash. You may now execute via SuperPlane."]);
    } catch (e) {
      setExecOut([String(e)]);
    } finally {
      setApproving(false);
    }
  };

  const runExecute = async () => {
    if (!approvedHash || !sanitized) return;
    setStreaming(true); setExecOut([]);
    try {
      const r = await fetch(`${apiBase}/execute/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Session-Id": sessionId },
        body: JSON.stringify({ content: sanitized, content_hash: approvedHash }),
      });
      if (!r.ok) throw new Error(await r.text());
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
              if (evt.type === "output") setExecOut((prev) => [...prev, evt.data]);
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

  const renderSandbox = () => (
    <div className="content-scroll">
      <div className="grid-2">
        <div className="glass-panel">
          <div className="panel-header"><Activity size={16}/> LIVE TELEMETRY STREAM</div>
          <pre className="terminal" ref={logBoxRef} style={{ maxHeight: "350px", height: "300px" }}>
            {logs.length > 0 ? logs.join("\n") : "Awaiting telemetry..."}
          </pre>
        </div>

        <div className="glass-panel" style={{ display: "flex", flexDirection: "column" }}>
          <div className="panel-header"><Cpu size={16}/> INCIDENT CONTEXT</div>
          <label className="input-label">Operator Description</label>
          <textarea value={incident} onChange={(e) => setIncident(e.target.value)} style={{ minHeight: "120px", flex: 1, marginBottom: "1rem" }} />
          <button className="btn primary" disabled={execBusy} onClick={runAnalyze} style={{ width: "100%", padding: "1rem", fontSize: "1rem" }}>
            <Plane size={18} /> {loading ? "Analyzing Models..." : "SuperPlane Analysis"}
          </button>
        </div>
      </div>

      {(analysis || rawRunbook) && (
        <div className="grid-2">
          <div className="glass-panel">
            <div className="panel-header"><FileJson size={16}/> AI ROOT CAUSE ANALYSIS</div>
            <pre className="terminal stream-sys" style={{ whiteSpace: "pre-wrap" }}>{analysis}</pre>
          </div>
          <div className="glass-panel">
            <div className="panel-header"><Terminal size={16}/> AI GENERATED RUNBOOK</div>
            <pre className="terminal">{rawRunbook}</pre>
          </div>
        </div>
      )}

      {sanitized && (
        <div className="glass-panel" style={{ border: isApproved ? '1px solid var(--accent-green)' : undefined }}>
          <div className="panel-header" style={{ color: isApproved ? 'var(--accent-green)' : undefined }}>
            <ShieldAlert size={16}/> VERIGUARD POLICY ENFORCEMENT {revalidating && "(evaluating...)"}
          </div>
          
          {blocked.length > 0 ? (
            <ul className="policy-list">
              {blocked.map((b) => (
                <li key={b.line_number}>
                  <span className="badge blocked">L{b.line_number}</span>
                  {b.reason}: <code>{b.line}</code>
                </li>
              ))}
            </ul>
          ) : (
            <div style={{ padding: "0.75rem", background: "rgba(0, 230, 118, 0.1)", color: "var(--accent-green)", borderRadius: "8px", marginBottom: "1rem", display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.9rem", fontWeight: 500 }}>
              <CheckCircle2 size={16} /> 0 Policy Violations Detected in Sandbox
            </div>
          )}

          <label className="input-label">Sanitized Execution Payload (Editable Hash Target)</label>
          <textarea value={sanitized} onChange={(e) => handleSanitizedChange(e.target.value)} style={{ fontFamily: "'Fira Code', monospace", marginBottom: "1.5rem" }} />

          <div style={{ display: "flex", gap: "1rem", alignItems: "center" }}>
            <button className={`btn ${isApproved ? 'success' : ''}`} disabled={execBusy || !hasSanitized || isApproved} onClick={runApprove} style={{ background: isApproved ? "var(--accent-green)" : "rgba(255,255,255,0.1)" }}>
              {approving ? "Hashing..." : isApproved ? <><CheckCircle2 size={18}/> Payload Approved</> : "Sign & Approve Payload"}
            </button>
            
            <button className="btn primary" disabled={execBusy || !isApproved} onClick={runExecute}>
              <Play size={18} /> {streaming ? "Executing securely..." : "Launch SuperPlane Sandbox"}
            </button>
          </div>
        </div>
      )}

      {(execOut.length > 0) && (
        <div className="glass-panel">
          <div className="panel-header"><Terminal size={16}/> SUPERPLANE EPHEMERAL SANDBOX OUTPUT</div>
          <pre className="terminal" ref={termBoxRef}>
            {execOut.map((line, i) => (
              <div key={i} className={line.includes("✅") ? "stream-ok" : line.includes("🚫") ? "stream-err" : line.includes("✈️") ? "stream-sys" : ""}>
                {line}
              </div>
            ))}
          </pre>
        </div>
      )}
    </div>
  );

  return (
    <div className="app-container">
      {/* Sidebar View */}
      <div className="sidebar">
        <div className="sidebar-header">
          <Plane size={22} color="var(--accent-blue)" style={{ transform: "rotate(-45deg)" }}/>
          DevOps AI Platform
        </div>
        <div className="sidebar-nav">
          <button className={`nav-btn ${activeTab === 'sandbox' ? 'active' : ''}`} onClick={() => setActiveTab('sandbox')}>
            <Terminal className="nav-icon"/> SuperPlane Sandbox
          </button>
          <button className={`nav-btn ${activeTab === 'grafana' ? 'active' : ''}`} onClick={() => setActiveTab('grafana')}>
            <Activity className="nav-icon"/> System Telemetry
          </button>
          <button className={`nav-btn ${activeTab === 'prometheus' ? 'active' : ''}`} onClick={() => setActiveTab('prometheus')}>
            <Database className="nav-icon"/> Raw Metrics Database
          </button>
          <button className={`nav-btn ${activeTab === 'api' ? 'active' : ''}`} onClick={() => setActiveTab('api')}>
            <FileJson className="nav-icon"/> API Contracts
          </button>
        </div>
      </div>

      {/* Main Container */}
      <div className="main-content">
        <div className="top-nav">
          <div className="top-title">
            {activeTab === 'sandbox' && "Secure Execution Engine"}
            {activeTab === 'grafana' && "Grafana Observability Suite"}
            {activeTab === 'prometheus' && "Prometheus Time Series Metrics Engine"}
            {activeTab === 'api' && "System API Specifications"}
          </div>
        </div>
        
        {/* Router emulation */}
        {activeTab === 'sandbox' && renderSandbox()}
        {activeTab === 'grafana' && (
          <iframe className="iframe-container" src="http://localhost:3002/d/devops-ai-services/devops-ai-e28094-services?orgId=1&kiosk=tv&theme=dark" />
        )}
        {activeTab === 'prometheus' && (
          <iframe className="iframe-container" src="http://localhost:9090/" />
        )}
        {activeTab === 'api' && (
          <iframe className="iframe-container" src="http://localhost:8000/docs" />
        )}
      </div>
    </div>
  );
}
