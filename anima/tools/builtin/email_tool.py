"""Email tool — send and read emails via SMTP/IMAP."""

from __future__ import annotations

import asyncio
import email
import imaplib
import json
import smtplib
from email.mime.text import MIMEText
from email.header import decode_header
from pathlib import Path

from anima.config import data_dir
from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("tools.email")

# Email config — loaded from data/credentials/email.json or hardcoded defaults
_EMAIL_CONFIG = {
    "account": "",
    "password": "",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "contacts": {},
}
# Actual credentials loaded from data/credentials/email.json at runtime


def _load_config():
    """Load email config from file if exists."""
    global _EMAIL_CONFIG
    p = data_dir() / "credentials" / "email.json"
    if p.exists():
        try:
            _EMAIL_CONFIG.update(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:
            log.debug("_load_config: %s", e)


def _send_sync(to: str, subject: str, body: str) -> dict:
    """Send an email via SMTP."""
    _load_config()
    cfg = _EMAIL_CONFIG

    # Resolve contact names
    if to in cfg.get("contacts", {}):
        to = cfg["contacts"][to]

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = cfg["account"]
        msg["To"] = to
        msg["Subject"] = subject

        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
            server.starttls()
            server.login(cfg["account"], cfg["password"])
            server.send_message(msg)

        log.info("Email sent to %s: %s", to, subject)
        return {"success": True, "message": f"Sent to {to}: {subject}"}
    except Exception as e:
        log.error("Email send failed: %s", e)
        return {"success": False, "error": str(e)}


def _read_sync(folder: str = "INBOX", limit: int = 5, unread_only: bool = True) -> dict:
    """Read recent emails via IMAP."""
    _load_config()
    cfg = _EMAIL_CONFIG

    try:
        mail = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
        mail.login(cfg["account"], cfg["password"])
        mail.select(folder)

        criteria = "UNSEEN" if unread_only else "ALL"
        _, data = mail.search(None, criteria)
        ids = data[0].split()

        if not ids:
            mail.logout()
            return {"emails": [], "count": 0, "message": "No emails found"}

        # Get latest N
        recent_ids = ids[-limit:]
        emails = []

        for eid in reversed(recent_ids):
            _, msg_data = mail.fetch(eid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])

            subject = ""
            raw_subject = msg.get("Subject", "")
            if raw_subject:
                decoded = decode_header(raw_subject)
                subject = "".join(
                    part.decode(enc or "utf-8") if isinstance(part, bytes) else part
                    for part, enc in decoded
                )

            from_addr = msg.get("From", "")
            date = msg.get("Date", "")

            # Get body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

            emails.append({
                "from": from_addr,
                "subject": subject,
                "date": date,
                "body": body[:500],
            })

        mail.logout()
        return {"emails": emails, "count": len(emails)}

    except Exception as e:
        log.error("Email read failed: %s", e)
        return {"success": False, "error": str(e)}


async def _send_email(to: str, subject: str, body: str) -> dict:
    return await asyncio.get_event_loop().run_in_executor(None, _send_sync, to, subject, body)


async def _read_email(folder: str = "INBOX", limit: int = 5, unread_only: bool = True) -> dict:
    return await asyncio.get_event_loop().run_in_executor(None, _read_sync, folder, limit, unread_only)


def get_email_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="send_email",
            description=(
                "Send an email. Can use contact names defined in credentials. "
                "Uses eva0.agent@gmail.com as sender."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email or contact name"},
                    "subject": {"type": "string", "description": "Email subject"},
                    "body": {"type": "string", "description": "Email body text"},
                },
                "required": ["to", "subject", "body"],
            },
            risk_level=RiskLevel.MEDIUM,
            handler=_send_email,
        ),
        ToolSpec(
            name="read_email",
            description="Read recent emails from Eva's Gmail inbox.",
            parameters={
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "default": "INBOX"},
                    "limit": {"type": "integer", "default": 5},
                    "unread_only": {"type": "boolean", "default": True},
                },
            },
            risk_level=RiskLevel.SAFE,
            handler=_read_email,
        ),
    ]
