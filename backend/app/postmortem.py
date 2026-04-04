import os
import smtplib
from email.message import EmailMessage
from fpdf import FPDF
from typing import Dict, Any

def create_post_mortem_pdf(data: Dict[str, Any]) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    
    # Title
    pdf.set_font("helvetica", 'B', 16)
    pdf.cell(0, 10, "Incident Post-Mortem Report", align='C')
    pdf.ln(15)

    def add_section(title: str, content: str):
        # fpdf standard fonts only support latin-1. Emojis will crash it.
        safe_title = title.encode('latin-1', 'replace').decode('latin-1')
        safe_content = str(content).encode('latin-1', 'replace').decode('latin-1')

        pdf.set_font("helvetica", 'B', 14)
        pdf.set_text_color(58, 134, 255) # Accent blue
        pdf.cell(0, 10, safe_title)
        pdf.ln(10)
        
        pdf.set_font("helvetica", size=11)
        pdf.set_text_color(0, 0, 0)
        # Use multi_cell for wrapping text
        pdf.multi_cell(0, 6, safe_content)
        pdf.ln(5)

    add_section("Incident Timing", f"Start: {data.get('start_time', 'N/A')}\nEnd: {data.get('end_time', 'N/A')}")
    add_section("Incident Description", data.get('incident_description', 'No description provided.'))
    add_section("AI Diagnosis", data.get('analysis', 'No analysis available.'))
    add_section("Fix Applied (Runbook)", data.get('runbook', 'No runbook executed.'))
    
    # For execution output, we truncate if it's too long
    out_text = data.get('output', '')
    if len(out_text) > 3000:
        out_text = out_text[:3000] + "\n...[truncated]"
    add_section("Execution Output", out_text)

    return bytes(pdf.output())

def send_post_mortem_email(to_email: str, pdf_bytes: bytes, incident_desc: str):
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")

    if not smtp_server or not smtp_user or not smtp_pass:
        print("[postmortem] SMTP credentials not fully configured. Email sending skipped.")
        return False

    msg = EmailMessage()
    msg['Subject'] = 'Automated Post-Mortem: Incident Resolved'
    msg['From'] = smtp_user
    msg['To'] = to_email
    
    body = f"""
Hello,

An incident has been resolved. The automated post-mortem report is attached.

Incident Summary:
{incident_desc}

Best,
DevOps AI Platform
"""
    msg.set_content(body)
    msg.add_attachment(pdf_bytes, maintype='application', subtype='pdf', filename='post_mortem.pdf')

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        print(f"[postmortem] Email successfully sent to {to_email}")
        return True
    except Exception as e:
        print(f"[postmortem] Failed to send email: {e}")
        return False
