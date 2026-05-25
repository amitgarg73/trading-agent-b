"""
Alert dispatcher. ntfy.sh is the primary channel; Gmail is the silent fallback.
If both fail, the failure is recorded in the local ledger.
"""
from __future__ import annotations
import os
import smtplib
from email.mime.text import MIMEText


def _try_gmail(subject: str, body: str) -> bool:
    gmail_user     = os.getenv("GMAIL_USER")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")
    to_email       = os.getenv("ALERT_EMAIL", gmail_user)

    if not gmail_user or not gmail_password:
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"]    = gmail_user
    msg["To"]      = to_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_user, gmail_password)
            smtp.send_message(msg)
        return True
    except Exception:
        return False


def send_alert(subject: str, body: str) -> None:
    """Dispatch an alert. ntfy first, Gmail fallback. Logs to ledger if both fail."""
    from core import ntfy    # local import avoids circular dependency
    from core import ledger

    if ntfy.send_alert(subject, body):
        return

    if _try_gmail(subject, body):
        print(f"  ✉️  Gmail fallback sent: {subject}")
        return

    print(f"  ⚠️  All alert channels failed: {subject}")
    ledger.log("alert_delivery_failed", {"subject": subject})
