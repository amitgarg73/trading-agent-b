"""
Lightweight email alert utility for trading agent runs.
Uses the same GMAIL_USER / GMAIL_APP_PASSWORD / ALERT_EMAIL env vars as health_check.py.
"""
import os
import smtplib
from email.mime.text import MIMEText


def send_alert(subject: str, body: str) -> None:
    gmail_user     = os.getenv("GMAIL_USER")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")
    to_email       = os.getenv("ALERT_EMAIL", gmail_user)

    if not gmail_user or not gmail_password:
        print(f"  ⚠️  Alert not sent (email not configured): {subject}")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"]    = gmail_user
    msg["To"]      = to_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_user, gmail_password)
            smtp.send_message(msg)
        print(f"  ✉️  Alert sent: {subject}")
    except Exception as e:
        print(f"  ⚠️  Alert email failed ({subject}): {e}")
