"""
ntfy.sh alert channel. Requires NTFY_TOPIC env var (e.g. "amitgarg-trading-abc123").
Optionally set NTFY_SERVER to self-host; defaults to https://ntfy.sh.
Mirrors the send_alert(subject, body) interface from alerts.py.
Uses urllib only — no extra dependencies.
"""
from __future__ import annotations
import os
import urllib.request


_NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")


def send_alert(subject: str, body: str) -> bool:
    """POST to ntfy.sh topic. Returns True on delivery, False on any failure.
    Logs alert_delivery_failed to the local ledger on exception; never raises."""
    topic = os.getenv("NTFY_TOPIC")

    if not topic:
        print(f"  ⚠️  ntfy not configured (NTFY_TOPIC not set): {subject}")
        return False

    try:
        req = urllib.request.Request(
            f"{_NTFY_SERVER}/{topic}",
            data=body.encode("utf-8"),
            headers={
                "Title":        subject,
                "Priority":     "high",
                "Content-Type": "text/plain",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print(f"  📱 ntfy sent: {subject}")
                return True
            return False
    except Exception as e:
        print(f"  ⚠️  ntfy failed ({subject}): {e}")
        try:
            from core import ledger
            ledger.log("alert_delivery_failed", {
                "channel": "ntfy",
                "subject": subject,
                "error":   str(e),
            })
        except Exception:
            pass
        return False
