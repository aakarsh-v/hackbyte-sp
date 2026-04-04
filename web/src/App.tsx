import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import {
  Plane,
  Activity,
  FileJson,
  Play,
  ShieldAlert,
  Cpu,
  Terminal,
  CheckCircle2,
  Database,
  MessageSquare,
  Bot,
  FileText,
  Download,
} from "lucide-react";

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

type LogEvent = { time: string; service: string; level: string; message: string };
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
  const [activeTab, setActiveTab] = useState<"sandbox" | "grafana" | "prometheus" | "api" | "query" | "postmortem">(
    "sandbox",
  );
  const [sessionId, setSessionId] = useState(() => getOrCreateSessionId());
  const [logs, setLogs] = useState<string[]>([]);
  const [incident, setIncident] = useState(
    "EC2 production server critical failure: Nginx 502 Bad Gateway, payment-service OOM killed (exit code 137), database connection refused on db:5432. Multiple upstream retries exhausted.",
  );
  const [metricsHint, setMetricsHint] = useState("");
  const [includePrometheus, setIncludePrometheus] = useState(false);
  const [imageBase64, setImageBase64] = useState("");
  const [imageMime, setImageMime] = useState("image/png");
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);
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
  const [nlQuestion, setNlQuestion] = useState("");
  const [nlAnswer, setNlAnswer] = useState("");
  const [nlLoading, setNlLoading] = useState(false);
  const [includeRunbookHints, setIncludeRunbookHints] = useState(true);
  const [incidentStartTime, setIncidentStartTime] = useState<string | null>(null);
  
  // Post-Mortem specific state
  const [pmGenerating, setPmGenerating] = useState(false);
  const [pmResult, setPmResult] = useState<{file_path: string, email_sent: boolean} | null>(null);
  const [pmError, setPmError] = useState<string | null>(null);

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

  const onImageFile = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) {
      setImageBase64("");
      setImageMime("image/png");
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
      setImagePreviewUrl(null);
      return;
    }
    if (f.size > 5 * 1024 * 1024) {
      setAnalysis("Image too large (max 5MB).");
      return;
    }
    const mime = f.type || "image/png";
    setImageMime(mime);
    const reader = new FileReader();
    reader.onload = () => {
      const data = reader.result as string;
      const b64 = data.includes(",") ? data.split(",")[1]! : data;
      setImageBase64(b64);
      setImagePreviewUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(f);
      });
    };
    reader.readAsDataURL(f);
  };

  const clearImage = () => {
    setImageBase64("");
    setImageMime("image/png");
    if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    setImagePreviewUrl(null);
  };

  const startNewSession = () => {
    try {
      const id = crypto.randomUUID();
      localStorage.setItem(SESSION_STORAGE_KEY, id);
      setSessionId(id);
    } catch {
      setSessionId("00000000-0000-0000-0000-000000000001");
    }
    setApprovedHash(null);
    setExecOut(["New incident session — run Analyze to draft a runbook, then Approve → Execute."]);
    setSanitized("");
    setSanitizedHash(null);
    setBlocked([]);
    setAnalysis("");
    setRawRunbook("");
    setIncidentStartTime(null);
    setPmResult(null);
    setPmError(null);
  };

  const runAnalyze = async () => {
    setLoading(true);
    setExecOut([]);
    setApprovedHash(null);
    if (!incidentStartTime) {
      setIncidentStartTime(new Date().toLocaleString());
    }
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
          include_prometheus_snapshot: includePrometheus,
          image_base64: imageBase64,
          image_mime_type: imageMime,
        }),
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

  const runIncidentQuery = async () => {
    const q = nlQuestion.trim();
    if (!q) return;
    setNlLoading(true);
    setNlAnswer("");
    try {
      const r = await fetch(`${apiBase}/incident-query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: q,
          log_limit: 600,
          include_runbook_hints: includeRunbookHints,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      const data: { answer: string } = await r.json();
      setNlAnswer(data.answer);
    } catch (e) {
      setNlAnswer(String(e));
    } finally {
      setNlLoading(false);
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
        const newHash = Array.from(new Uint8Array(hashBuf))
          .map((b) => b.toString(16).padStart(2, "0"))
          .join("");
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
      setExecOut([
        "✅ Runbook computationally approved matching Hash. You may now execute via SuperPlane.",
      ]);
    } catch (e) {
      setExecOut([String(e)]);
    } finally {
      setApproving(false);
    }
  };

  const runExecute = async () => {
    if (!approvedHash || !sanitized) return;
    setStreaming(true);
    setExecOut([]);
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
              if (evt.type === "output") {
                setExecOut((prev) => {
                  const newOut = [...prev, evt.data];
                  return newOut;
                });
              } else if (evt.type === "done") {
                // Done event, we don't automatically trigger PM anymore
                setExecOut((prev) => [...prev, "✈️ Sandbox execution finished. You can now generate a Post-Mortem from the sidebar."]);
              }
            } catch {
              /* ignore */
            }
          }
        }
      }
    } catch (e) {
      setExecOut([String(e)]);
    } finally {
      setStreaming(false);
    }
  };

  const triggerPostMortem = async () => {
    setPmGenerating(true);
    setPmResult(null);
    setPmError(null);
    try {
      const endTime = new Date().toLocaleString();
      const finalOutput = termBoxRef.current?.innerText || execOut.join("\n") || "No output recorded.";
      
      const r = await fetch(`${apiBase}/post-mortem`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          incident_description: incident,
          analysis: analysis || "No AI analysis performed.",
          runbook: sanitized || rawRunbook || "No runbook generated.",
          output: finalOutput,
          start_time: incidentStartTime || "Unknown",
          end_time: endTime
        }),
      });
      
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      
      if (data.status === "ok") {
        setPmResult({ file_path: data.file_path, email_sent: data.email_sent });
      } else {
        throw new Error(data.detail || "Unknown backend error");
      }
    } catch (e) {
      setPmError(String(e));
    } finally {
      setPmGenerating(false);
    }
  };

  const isApproved = !!approvedHash;
  const hasSanitized = sanitized.trim().length > 0;
  const execBusy = loading || streaming || approving;

  const renderSandbox = () => (
    <div className="content-scroll">
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: "0.75rem",
          marginBottom: "1rem",
        }}
      >
        <span style={{ color: "var(--muted, #888)", fontSize: "0.85rem" }}>
          Session{" "}
          <code title={sessionId}>
            {sessionId.slice(0, 8)}…{sessionId.slice(-4)}
          </code>
        </span>
        <button type="button" className="btn" onClick={startNewSession} disabled={execBusy}>
          New incident session
        </button>
      </div>

      <div className="grid-2">
        <div className="glass-panel">
          <div className="panel-header">
            <Activity size={16} /> LIVE TELEMETRY STREAM
          </div>
          <pre
            className="terminal"
            ref={logBoxRef}
            style={{ maxHeight: "350px", height: "300px" }}
          >
            {logs.length > 0 ? logs.join("\n") : "Awaiting telemetry..."}
          </pre>
        </div>

        <div className="glass-panel" style={{ display: "flex", flexDirection: "column" }}>
          <div className="panel-header">
            <Cpu size={16} /> INCIDENT CONTEXT
          </div>
          <label className="input-label">Operator Description</label>
          <textarea
            value={incident}
            onChange={(e) => setIncident(e.target.value)}
            style={{ minHeight: "120px", flex: 1, marginBottom: "0.75rem" }}
          />
          <label className="input-label">Metrics / notes (optional)</label>
          <textarea
            value={metricsHint}
            onChange={(e) => setMetricsHint(e.target.value)}
            style={{ minHeight: "72px", marginBottom: "0.5rem" }}
          />
          <label
            style={{
              marginBottom: "0.75rem",
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              checked={includePrometheus}
              onChange={(e) => setIncludePrometheus(e.target.checked)}
            />
            <span>Attach live Prometheus snapshot (backend queries Prometheus)</span>
          </label>
          <label className="input-label">Screenshot / diagram (optional, max 5MB)</label>
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            onChange={onImageFile}
            style={{ marginBottom: "0.5rem" }}
          />
          {imagePreviewUrl && (
            <div
              style={{
                marginBottom: "1rem",
                display: "flex",
                alignItems: "center",
                gap: "0.75rem",
                flexWrap: "wrap",
              }}
            >
              <img
                src={imagePreviewUrl}
                alt="Upload preview"
                style={{
                  maxHeight: "120px",
                  maxWidth: "100%",
                  borderRadius: "4px",
                  border: "1px solid var(--border, #333)",
                }}
              />
              <button type="button" className="btn" onClick={clearImage}>
                Remove image
              </button>
            </div>
          )}
          <button
            className="btn primary"
            disabled={execBusy}
            onClick={runAnalyze}
            style={{ width: "100%", padding: "1rem", fontSize: "1rem" }}
          >
            <Plane size={18} /> {loading ? "Analyzing Models..." : "SuperPlane Analysis"}
          </button>
        </div>
      </div>


      {(analysis || rawRunbook) && (
        <div className="grid-2">
          <div className="glass-panel">
            <div className="panel-header">
              <FileJson size={16} /> AI ROOT CAUSE ANALYSIS
            </div>
            <pre className="terminal stream-sys" style={{ whiteSpace: "pre-wrap" }}>
              {analysis}
            </pre>
          </div>
          <div className="glass-panel">
            <div className="panel-header">
              <Terminal size={16} /> AI GENERATED RUNBOOK
            </div>
            <pre className="terminal">{rawRunbook}</pre>
          </div>
        </div>
      )}

      {sanitized && (
        <div
          className="glass-panel"
          style={{ border: isApproved ? "1px solid var(--accent-green)" : undefined }}
        >
          <div
            className="panel-header"
            style={{ color: isApproved ? "var(--accent-green)" : undefined }}
          >
            <ShieldAlert size={16} /> VERIGUARD POLICY ENFORCEMENT{" "}
            {revalidating && "(evaluating...)"}
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
            <div
              style={{
                padding: "0.75rem",
                background: "rgba(0, 230, 118, 0.1)",
                color: "var(--accent-green)",
                borderRadius: "8px",
                marginBottom: "1rem",
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                fontSize: "0.9rem",
                fontWeight: 500,
              }}
            >
              <CheckCircle2 size={16} /> 0 Policy Violations Detected in Sandbox
            </div>
          )}

          <label className="input-label">Sanitized Execution Payload (Editable Hash Target)</label>
          <textarea
            value={sanitized}
            onChange={(e) => handleSanitizedChange(e.target.value)}
            style={{ fontFamily: "'Fira Code', monospace", marginBottom: "1.5rem" }}
          />

          <div style={{ display: "flex", gap: "1rem", alignItems: "center", flexWrap: "wrap" }}>
            <button
              className={`btn ${isApproved ? "success" : ""}`}
              disabled={execBusy || !hasSanitized || isApproved}
              onClick={runApprove}
              style={{
                background: isApproved ? "var(--accent-green)" : "rgba(255,255,255,0.1)",
              }}
            >
              {approving ? (
                "Hashing..."
              ) : isApproved ? (
                <>
                  <CheckCircle2 size={18} /> Payload Approved
                </>
              ) : (
                "Sign & Approve Payload"
              )}
            </button>

            <button
              className="btn primary"
              disabled={execBusy || !isApproved}
              onClick={runExecute}
            >
              <Play size={18} /> {streaming ? "Executing securely..." : "Launch SuperPlane Sandbox"}
            </button>
          </div>
        </div>
      )}

      {execOut.length > 0 && (
        <div className="glass-panel">
          <div className="panel-header">
            <Terminal size={16} /> SUPERPLANE EPHEMERAL SANDBOX OUTPUT
          </div>
          <pre className="terminal" ref={termBoxRef}>
            {execOut.map((line, i) => (
              <div
                key={i}
                className={
                  line.includes("✅")
                    ? "stream-ok"
                    : line.includes("🚫")
                      ? "stream-err"
                      : line.includes("✈️")
                        ? "stream-sys"
                        : ""
                }
              >
                {line}
              </div>
            ))}
          </pre>
        </div>
      )}
    </div>
  );

  const renderQuery = () => (
    <div className="content-scroll">
      <div className="glass-panel" style={{ maxWidth: "800px", margin: "0 auto 1.5rem" }}>
        <div className="panel-header" style={{ fontSize: "1rem", marginBottom: "1.25rem" }}>
          <MessageSquare size={18} style={{ color: "var(--accent-blue)" }} />
          <span style={{ color: "var(--text-primary)" }}>Ask your logs (natural language)</span>
        </div>
        <p
          style={{
            fontSize: "0.9rem",
            color: "var(--text-secondary)",
            marginBottom: "1.25rem",
            lineHeight: 1.6,
            background: "rgba(58,134,255,0.06)",
            border: "1px solid rgba(58,134,255,0.15)",
            borderRadius: "8px",
            padding: "0.85rem 1rem",
          }}
        >
          <Bot size={14} style={{ display: "inline", verticalAlign: "middle", marginRight: "0.4rem", color: "var(--accent-blue)" }} />
          Answers use the same stored log excerpt as the backend (plus optional recent runbook
          snippets). Questions about fix duration or MTTR need timestamps in the logs;
          approved runbook history has no per-row timing in the database.
        </p>

        <label className="input-label">Your question</label>
        <textarea
          value={nlQuestion}
          onChange={(e) => setNlQuestion(e.target.value)}
          placeholder='e.g. "How many payment-service errors appear in the recent logs?"'
          style={{ minHeight: "120px", marginBottom: "0.75rem", width: "100%" }}
        />

        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
            cursor: "pointer",
            marginBottom: "1rem",
            fontSize: "0.9rem",
          }}
        >
          <input
            type="checkbox"
            checked={includeRunbookHints}
            onChange={(e) => setIncludeRunbookHints(e.target.checked)}
          />
          <span>Include recent runbook hints (truncated)</span>
        </label>

        <button
          type="button"
          className="btn primary"
          disabled={nlLoading || !nlQuestion.trim()}
          onClick={runIncidentQuery}
          style={{ marginBottom: "1rem", width: "100%", padding: "1rem", fontSize: "1rem" }}
        >
          <MessageSquare size={18} />
          {nlLoading ? "Thinking…" : "Ask"}
        </button>

        {nlAnswer && (
          <div>
            <div className="panel-header" style={{ marginTop: "0.5rem" }}>
              <Bot size={14} /> AI RESPONSE
            </div>
            <pre
              className="terminal stream-sys"
              style={{ whiteSpace: "pre-wrap", maxHeight: "400px", overflow: "auto" }}
            >
              {nlAnswer}
            </pre>
          </div>
        )}
      </div>
    </div>
  );

  const renderPostMortem = () => (
    <div className="content-scroll">
      <div className="glass-panel" style={{ maxWidth: "800px", margin: "0 auto" }}>
        <div className="panel-header" style={{ fontSize: "1rem", marginBottom: "1.25rem" }}>
          <FileText size={18} style={{ color: "var(--accent-blue)" }} />
          <span style={{ color: "var(--text-primary)" }}>Generate Incident Post-Mortem</span>
        </div>
        
        <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem", marginBottom: "1.5rem", lineHeight: 1.6 }}>
          Automatically generate a detailed PDF report containing the incident timeline, root cause analysis, 
          the executed runbook, and the final telemetry output. The report will be emailed to configured recipients.
        </p>

        <div style={{ background: "rgba(0,0,0,0.2)", padding: "1.5rem", borderRadius: "8px", marginBottom: "1.5rem", border: "1px solid var(--border-color)" }}>
          <h4 style={{ color: "var(--text-primary)", marginBottom: "1rem", fontSize: "0.9rem", textTransform: "uppercase" }}>Incident Context to be Included:</h4>
          <ul style={{ listStyleType: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "0.75rem", fontSize: "0.9rem", color: "var(--text-secondary)" }}>
            <li><strong>Start Time:</strong> {incidentStartTime || "Not recorded yet (Run Analysis)"}</li>
            <li><strong>AI Analysis:</strong> {analysis ? "Captured ✅" : "Missing ❌"}</li>
            <li><strong>Fix Applied:</strong> {sanitized || rawRunbook ? "Captured ✅" : "Missing ❌"}</li>
            <li><strong>Execution Output:</strong> {execOut.length > 0 ? "Captured ✅" : "Missing ❌"}</li>
          </ul>
        </div>

        <button
          className="btn primary"
          style={{ width: "100%", padding: "1rem", fontSize: "1rem", marginBottom: "1.5rem" }}
          onClick={triggerPostMortem}
          disabled={pmGenerating}
        >
          <FileText size={18} />
          {pmGenerating ? "Generating & Sending..." : "Generate Post-Mortem Report"}
        </button>

        {pmResult && (
          <div style={{ padding: "1.25rem", background: "rgba(0, 230, 118, 0.1)", border: "1px solid var(--accent-green)", borderRadius: "8px", color: "var(--text-primary)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1rem", color: "var(--accent-green)", fontWeight: 600 }}>
              <CheckCircle2 size={20} />
              Report Successfully Generated!
            </div>
            
            <ul style={{ listStyleType: "none", display: "flex", flexDirection: "column", gap: "0.5rem", fontSize: "0.9rem", marginBottom: "1.5rem" }}>
              <li><strong>Local File Saved:</strong> <code>backend/{pmResult.file_path}</code></li>
              <li><strong>Email Notification:</strong> {pmResult.email_sent ? "Sent successfully to jyotiradityatripathi67@gmail.com" : "Skipped/Failed (Check terminal logs for SMTP errors)"}</li>
            </ul>

            <a href={`${apiBase}/${pmResult.file_path.replace("post_mortems/", "post_mortems/")}`} download target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
              <button className="btn success" style={{ width: "auto" }}>
                <Download size={16} /> Download PDF
              </button>
            </a>
          </div>
        )}

        {pmError && (
          <div style={{ padding: "1rem", background: "rgba(255, 51, 102, 0.1)", border: "1px solid var(--accent-red)", borderRadius: "8px", color: "var(--accent-red)", fontSize: "0.9rem" }}>
            <strong>Error:</strong> {pmError}
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div className="app-container">
      <div className="sidebar">
        <div className="sidebar-header">
          <Plane size={22} color="var(--accent-blue)" style={{ transform: "rotate(-45deg)" }} />
          DevOps AI Platform
        </div>
        <div className="sidebar-nav">
          <button
            className={`nav-btn ${activeTab === "sandbox" ? "active" : ""}`}
            onClick={() => setActiveTab("sandbox")}
          >
            <Terminal className="nav-icon" /> SuperPlane Sandbox
          </button>
          <button
            className={`nav-btn ${activeTab === "query" ? "active" : ""}`}
            onClick={() => setActiveTab("query")}
          >
            <MessageSquare className="nav-icon" /> Ask your logs
          </button>
          <button
            className={`nav-btn ${activeTab === "postmortem" ? "active" : ""}`}
            onClick={() => setActiveTab("postmortem")}
          >
            <FileText className="nav-icon" /> Post-Mortem Report
          </button>
          <button
            className={`nav-btn ${activeTab === "grafana" ? "active" : ""}`}
            onClick={() => setActiveTab("grafana")}
          >
            <Activity className="nav-icon" /> System Telemetry
          </button>
          <button
            className={`nav-btn ${activeTab === "prometheus" ? "active" : ""}`}
            onClick={() => setActiveTab("prometheus")}
          >
            <Database className="nav-icon" /> Raw Metrics Database
          </button>
          <button
            className={`nav-btn ${activeTab === "api" ? "active" : ""}`}
            onClick={() => setActiveTab("api")}
          >
            <FileJson className="nav-icon" /> API Contracts
          </button>
        </div>
      </div>

      <div className="main-content">
        <div className="top-nav">
          <div className="top-title">
            {activeTab === "sandbox" && "Secure Execution Engine"}
            {activeTab === "query" && "Ask Your Logs — Natural Language Query"}
            {activeTab === "postmortem" && "Automated Incident Post-Mortems"}
            {activeTab === "grafana" && "Grafana Observability Suite"}
            {activeTab === "prometheus" && "Prometheus Time Series Metrics Engine"}
            {activeTab === "api" && "System API Specifications"}
          </div>
        </div>

        {activeTab === "sandbox" && renderSandbox()}
        {activeTab === "query" && renderQuery()}
        {activeTab === "postmortem" && renderPostMortem()}
        {activeTab === "grafana" && (
          <iframe
            className="iframe-container"
            src="http://localhost:3002/d/devops-ai-services/devops-ai-e28094-services?orgId=1&kiosk=tv&theme=dark"
            title="Grafana"
          />
        )}
        {activeTab === "prometheus" && (
          <iframe className="iframe-container" src="http://localhost:9090/" title="Prometheus" />
        )}
        {activeTab === "api" && (
          <iframe className="iframe-container" src="http://localhost:8000/docs" title="API docs" />
        )}
      </div>
    </div>
  );
}
