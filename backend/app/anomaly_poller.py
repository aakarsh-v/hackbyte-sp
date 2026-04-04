import asyncio
import os
import httpx
import random
from typing import Callable, Any

from app.models import LogEvent, LogIngestBatch
import app.gemini_client as gemini_client
from app.persistence import fetch_log_tail, append_log_event
from app.prometheus_snapshot import build_metrics_snapshot

async def start_anomaly_poller(broadcast_callback: Callable[[LogIngestBatch], Any]):
    poll_interval = int(os.environ.get("ANOMALY_POLL_INTERVAL_SEC", "30"))
    threshold = float(os.environ.get("ANOMALY_ERROR_THRESHOLD", "0.03"))
    simulate = os.environ.get("SIMULATE_ANOMALIES", "true").lower() == "true"
    base_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

    print(f"[anomaly-poller] Started. Interval={poll_interval}s, Threshold={threshold}, Simulate={simulate}")

    queries = {
        "error_rate": 'sum(rate(http_requests_total{status=~"5.."}[2m])) / sum(rate(http_requests_total[2m]))'
    }
    
    # Cooldown multiplier (e.g. 5 means wait 5 * poll_interval before alerting again)
    cooldown = 0
    
    while True:
        await asyncio.sleep(poll_interval)
        if cooldown > 0:
            cooldown -= 1
            if cooldown == 0:
                print("[anomaly-poller] Cooldown ended, ready to look for anomalies again.")
            continue
            
        anomaly_detected = False
        incident_description = ""
        
        # 1. Check Prometheus natively
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{base_url}/api/v1/query", params={"query": queries["error_rate"]})
                if r.status_code == 200:
                    data = r.json()
                    results = data.get("data", {}).get("result", [])
                    if results:
                        value = float(results[0].get("value", [0, "0"])[1])
                        if value > threshold:
                            anomaly_detected = True
                            incident_description = f"Payment Service error rate automatically detected at {value*100:.1f}%, exceeding {threshold*100}% threshold. Prometheus metric breached."
        except Exception:
            pass # ignore timeouts and unreachability
            
        # 2. Add simulation logic for Demo wow factor
        if simulate and not anomaly_detected:
            # Randomly trigger an anomaly (roughly ~15% chance per 30s)
            if random.random() < 0.15:
                anomaly_detected = True
                simulated_rate = 3.8 + random.random() * 2.0
                incident_description = f"[SIMULATED] Payment service error rate just jumped from 0.1% to {simulated_rate:.1f}% in the last 2 minutes."
                
        if anomaly_detected:
            print("[anomaly-poller] 🚨 Anomaly threshold crossed! Triggering AI pre-emptive analysis...")
            
            # Formally alert the UI immediately
            alert_event = LogEvent(
                service="SuperPlane_Anomaly_Engine",
                level="CRITICAL",
                message=f"🚨 PROACTIVE ALERT: {incident_description}\nPre-emptively analyzing logs and mapping a fix..."
            )
            append_log_event(alert_event)
            await broadcast_callback(LogIngestBatch(events=[alert_event]))
            
            # 3. Harvest metrics and logs
            metrics_text = await build_metrics_snapshot(base_url)
            try:
                logs_list = await fetch_log_tail(80)
            except Exception:
                logs_list = []
            logs_text = "\n".join(f"[{evt.time}] {evt.service} {evt.level}: {evt.message}" for evt in logs_list)
            
            # 4. Request Gemini Auto-Runbook
            prompt = f"Automated Anomaly Detection Event: {incident_description}. Generate a RCA and runbook fix."
            try:
                ai_resp = await gemini_client.analyze_incident(
                    incident_description=prompt,
                    logs_text=logs_text,
                    metrics_hint=metrics_text,
                    image_base64="",
                    image_mime=""
                )
                
                result_msg = f"✅ PRE-EMPTIVE RUNBOOK MAPPED:\n\nRoot Cause: {ai_resp.analysis}\n\nSuggested Fix (Ready in Sandbox):\n{ai_resp.raw_runbook}"
                
                fix_event = LogEvent(
                    service="SuperPlane_AI",
                    level="ALERT",
                    message=result_msg
                )
                append_log_event(fix_event)
                await broadcast_callback(LogIngestBatch(events=[fix_event]))
                
            except Exception as e:
                err_event = LogEvent(
                    service="SuperPlane_Anomaly_Engine",
                    level="ERROR",
                    message=f"Failed to generate pre-emptive runbook: {str(e)}"
                )
                append_log_event(err_event)
                await broadcast_callback(LogIngestBatch(events=[err_event]))
                
            # Stay silent for approx 3 minutes after triggering an anomaly
            cooldown = 6
