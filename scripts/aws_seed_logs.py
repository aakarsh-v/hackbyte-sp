"""
aws_seed_logs.py  —  Seeds AWS CloudWatch with real-looking EC2 crash logs
AND simultaneously POSTs them directly to the DevOps AI backend /ingest endpoint
so they appear instantly in the live console UI.
"""
import boto3
import time
import os
import sys
import urllib.request
import json
from datetime import datetime, timezone

# ─── Read .env ─────────────────────────────────────────────────────────────
env_vars = {}
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if not os.path.exists(env_path):
    env_path = ".env"
try:
    with open(env_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()
except Exception as e:
    print(f"❌ Failed to read .env: {e}")
    sys.exit(1)

log_group   = env_vars.get("CW_LOG_GROUP", "/aws/ec2/your-app")
region      = env_vars.get("AWS_REGION", "us-east-1")
access_key  = env_vars.get("AWS_ACCESS_KEY_ID")
secret_key  = env_vars.get("AWS_SECRET_ACCESS_KEY")
backend_url = "http://localhost:8000/ingest"
stream_name = "i-0abc123def456/var/log/syslog"

# ─── Realistic EC2 crash scenario ─────────────────────────────────────────
EC2_LOGS = [
    ("INFO",     "[systemd] Starting Nginx HTTP server..."),
    ("INFO",     "[nginx] Worker process started (PID 2813)"),
    ("INFO",     "[app]   PaymentService initialized on port 8080"),
    ("WARN",     "[nginx] Upstream payment-service response time: 2847ms (threshold: 500ms)"),
    ("ERROR",    "[nginx] 502 Bad Gateway — upstream payment-service connection refused"),
    ("ERROR",    "[nginx] 502 Bad Gateway — upstream payment-service connection refused"),
    ("CRITICAL","[nginx] All upstream retries exhausted — marking payment-service DOWN"),
    ("ERROR",    "[app]   Unhandled exception: java.net.ConnectException: Connection refused to db:5432"),
    ("CRITICAL", "[systemd] payment-service.service: Main process exited, code=killed, status=137/KILL"),
]

def _infer_service(stream: str) -> str:
    parts = stream.split("/")
    if parts and parts[0].startswith("i-"):
        return f"ec2:{parts[0]}"
    return "cloudwatch"

def _infer_level(msg: str) -> str:
    m = msg.lower()
    if any(k in m for k in ["critical", "fatal", "killed"]): return "ERROR"
    if any(k in m for k in ["error", "502", "exception", "refused"]): return "ERROR"
    if any(k in m for k in ["warn"]): return "WARN"
    return "INFO"

# ─── Step 1: AWS CloudWatch ────────────────────────────────────────────────
if not access_key or not secret_key:
    print("❌ AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY not set in .env")
    sys.exit(1)

print(f"\n{'='*55}")
print(f" DevOps AI — AWS CloudWatch Demo Seeder")
print(f"{'='*55}")
print(f"📡 AWS Region:    {region}")
print(f"📁 Log Group:     {log_group}")
print(f"🔑 Access Key:    {access_key[:10]}...")
print(f"{'='*55}\n")

client = boto3.client("logs", region_name=region,
                      aws_access_key_id=access_key, aws_secret_access_key=secret_key)

# Create log group
try:
    print(f"📁 Creating CloudWatch Log Group: {log_group}")
    client.create_log_group(logGroupName=log_group)
    print("   ✅ Log Group created.")
except client.exceptions.ResourceAlreadyExistsException:
    print("   ✅ Log Group already exists.")
except Exception as e:
    print(f"   ⚠️  {e}")

# Create log stream
try:
    print(f"📄 Creating Log Stream: {stream_name}")
    client.create_log_stream(logGroupName=log_group, logStreamName=stream_name)
    print("   ✅ Log Stream created.")
except client.exceptions.ResourceAlreadyExistsException:
    print("   ✅ Log Stream already exists.")
except Exception as e:
    print(f"   ⚠️  {e}")

# Push log events to AWS
now_ms = int(time.time() * 1000)
cw_events = [
    {"timestamp": now_ms - (len(EC2_LOGS) - i) * 1500 + 100, "message": msg}
    for i, (_, msg) in enumerate(EC2_LOGS)
]
try:
    print(f"\n✍️  Pushing {len(cw_events)} EC2 logs to AWS CloudWatch...")
    client.put_log_events(logGroupName=log_group, logStreamName=stream_name, logEvents=cw_events)
    print("   ✅ Successfully pushed to AWS CloudWatch!")
    print("      (CloudWatch poller will pick these up within 15 seconds)")
except Exception as e:
    print(f"   ❌ Failed to push to CloudWatch: {e}")

# ─── Step 2: Direct POST to backend /ingest (instant UI display) ─────────
print(f"\n🚀 Also sending logs directly to DevOps AI console (instant)...")
service = _infer_service(stream_name)
success_count = 0
for i, (level, message) in enumerate(EC2_LOGS):
    ts_ms = now_ms - (len(EC2_LOGS) - i) * 1500
    dt    = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    ts    = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    payload = json.dumps({
        "time":    ts,
        "service": service,
        "level":   level,
        "message": message,
        "extra":   {"stream": stream_name, "source": "aws-cloudwatch"}
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            backend_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                success_count += 1
    except Exception as e:
        print(f"   ⚠️  Could not POST to backend: {e}")
        break
    time.sleep(0.1)

if success_count > 0:
    print(f"   ✅ Sent {success_count}/{len(EC2_LOGS)} events to DevOps AI console!")

print(f"\n{'='*55}")
print(f" 🎉 DONE! Check http://localhost:5173 NOW!")
print(f"    Look for: [{service}] logs in the Live Log Stream")
print(f"    Then click 'Analyze + runbook (Gemini)' for AI analysis!")
print(f"{'='*55}\n")
