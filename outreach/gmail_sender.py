"""
Gmail campaign sender for Product Zone International.

Behaviour:
- One campaign run contacts at most CAMPAIGN_BATCH_SIZE (10) buyers, taken
  from the front of the unsent list.
- Every recipient receives the SAME universal email plus the company
  presentation PDF.  There is no per-buyer personalization.
- Successfully sent addresses are removed from buyers.csv; failed addresses
  stay in buyers.csv for a later campaign.
- A process-wide lock prevents two concurrent campaign runs (e.g. a
  double-clicked button) from sending the same batch twice.
"""

import os
import time
import random
import html
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import logging

import pandas as pd

try:
    from .gmail_auth import get_smtp_connection
except ImportError:  # pragma: no cover - direct module import fallback
    from gmail_auth import get_smtp_connection

from activity_logging import (
    load_buyers,
    load_sent_log,
    log_send_attempt,
    remove_sent_buyers,
)

from config import (
    GMAIL_EMAIL,
    MIN_SEND_DELAY,
    MAX_SEND_DELAY,
    PRESENTATION_PATH,
    PRODUCT_NAME,
    DAILY_SEND_LIMIT,
    CAMPAIGN_BATCH_SIZE,
)

logger = logging.getLogger(__name__)

# Guards against two campaign runs executing at the same time.
_campaign_lock = threading.Lock()

# Exact local-part matches that are clearly system / registrar addresses.
_SYSTEM_LOCAL_PARTS = {
    "abuse",
    "domain",
    "domains",
    "hostmaster",
    "hostadmin",
    "privacy",
    "whois",
    "registrar",
    "postmaster",
    "mailer-daemon",
    "noreply",
    "no-reply",
    "donotreply",
    "bounce",
    "bounces",
    "notifications",
    "unsubscribe",
}

_PLACEHOLDER_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "xyz.com",
    "test.com",
    "test.org",
    "test.net",
}
_PLACEHOLDER_LOCAL_PARTS = {"test", "example", "sample", "dummy"}


class AttachmentError(Exception):
    """Raised when the campaign attachment cannot be loaded."""


def _is_system_address(email: str) -> bool:
    """Return True if the email's local part is a known system address."""
    local = email.split("@", 1)[0].lower() if "@" in email else email.lower()
    return local in _SYSTEM_LOCAL_PARTS


def _is_placeholder_address(email: str) -> bool:
    local, _, domain = email.strip().lower().partition("@")
    return domain in _PLACEHOLDER_DOMAINS or local in _PLACEHOLDER_LOCAL_PARTS


# ──────────────────────────────────────────────────────────────
# Email builder
# ──────────────────────────────────────────────────────────────


def _make_html_body(plain_body: str) -> str:
    """Convert a plain-text body to simple HTML (no tracking, no templates)."""
    escaped = html.escape(plain_body)
    html_body = escaped.replace("\n", "<br>\n")
    return (
        "<!DOCTYPE html><html><body style='font-family:Arial,sans-serif;"
        "font-size:14px;line-height:1.6;color:#222;'>\n"
        f"{html_body}\n"
        "</body></html>"
    )


def build_email(
    receiver_email: str,
    subject: str,
    body: str,
    attachment_path: str,
    cc_email: str = None,
) -> MIMEMultipart:
    """
    Build a MIME multipart email (text/plain + text/html) from the universal
    campaign content, with the company presentation attached.

    The message is identical for every recipient; no buyer data is merged in.
    """
    if not attachment_path or not os.path.exists(attachment_path):
        raise AttachmentError("attachment_error")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_EMAIL
    msg["To"] = receiver_email
    if cc_email and str(cc_email).strip():
        msg["Cc"] = cc_email.strip()

    msg.attach(MIMEText(body, "plain", "utf-8"))
    msg.attach(MIMEText(_make_html_body(body), "html", "utf-8"))

    try:
        with open(attachment_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        filename = os.path.basename(attachment_path)
        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    except OSError as e:
        logger.error(f"[Sender] could not attach {attachment_path}: {e}")
        raise AttachmentError("attachment_error") from e

    outer = MIMEMultipart("mixed")
    outer["Subject"] = subject
    outer["From"] = GMAIL_EMAIL
    outer["To"] = receiver_email
    if cc_email and str(cc_email).strip():
        outer["Cc"] = cc_email.strip()
    outer.attach(msg)
    outer.attach(part)
    return outer


# ──────────────────────────────────────────────────────────────
# Audience loader
# ──────────────────────────────────────────────────────────────


def load_unsent_buyers() -> list[dict]:
    """
    Load the next campaign batch: unsent buyers from buyers.csv, already
    deduplicated by email and cleaned of system/placeholder/WHOIS addresses.
    """
    buyers = load_buyers()
    if not buyers:
        return []

    sent_emails = load_sent_log()
    seen: set[str] = set()
    clean: list[dict] = []
    for buyer in buyers:
        email = str(buyer.get("email", "") or "").strip().lower()
        if not email or email in seen or email in sent_emails:
            continue
        if _is_system_address(email) or _is_placeholder_address(email):
            continue
        # WHOIS-derived addresses are kept in buyers.csv for review but are
        # not valid outreach recipients.
        if str(buyer.get("source_platform", "")).strip().lower() == "whois":
            continue
        buyer["email"] = email
        # Empty CSV cells come back from pandas as NaN; normalise them to "".
        for field in ("buyer_name", "company_name", "country", "website", "source_platform"):
            value = buyer.get(field)
            if value is None or (isinstance(value, float) and pd.isna(value)):
                buyer[field] = ""
            else:
                buyer[field] = str(value)
        seen.add(email)
        clean.append(buyer)
    return clean


def select_next_batch(buyers: list[dict], batch_size: int = CAMPAIGN_BATCH_SIZE):
    """Return at most `batch_size` buyers from the front of the unsent list."""
    return list(buyers[:batch_size])


# ──────────────────────────────────────────────────────────────
# Campaign runner
# ──────────────────────────────────────────────────────────────


def run_campaign(subject: str, body: str) -> dict:
    """
    Send one campaign batch.

    Returns a summary dict with keys:
      status       — 'completed' | 'busy' | 'no_buyers' | 'no_attachment' | 'auth_error'
      attempted    — number of recipients processed
      sent         — number of successfully sent emails (removed from buyers.csv)
      failed       — number of failed sends (kept in buyers.csv)
      batch_size   — maximum recipients allowed in one run
      remaining    — buyers still available after the run
      message      — human-readable result
    """
    presentation_path = PRESENTATION_PATH
    daily_send_limit = int(os.getenv("DAILY_SEND_LIMIT", DAILY_SEND_LIMIT))
    cc_email = os.getenv("CC_EMAIL", "")
    batch_limit = min(CAMPAIGN_BATCH_SIZE, max(daily_send_limit, 0))

    summary = {
        "status": "completed",
        "attempted": 0,
        "sent": 0,
        "failed": 0,
        "unlogged": 0,
        "batch_size": batch_limit,
        "remaining": 0,
        "message": "",
    }

    # ── Pre-flight: verify attachment ─────────────────────────
    if not presentation_path or not os.path.exists(presentation_path):
        logger.error(f"[Sender] presentation not found: {presentation_path}")
        summary["status"] = "no_attachment"
        summary["message"] = (
            f"Presentation file not found ({presentation_path}). "
            "Set PRESENTATION_PATH in .env and retry."
        )
        summary["remaining"] = len(load_unsent_buyers())
        return summary

    # ── Load and select the batch ─────────────────────────────
    unsent = load_unsent_buyers()
    if not unsent:
        summary["status"] = "no_buyers"
        summary["message"] = "No unsent buyers available for this campaign."
        return summary

    batch = select_next_batch(unsent, batch_limit)
    summary["remaining"] = len(unsent) - len(batch)

    # ── Single-run guard (double-click protection) ────────────
    if not _campaign_lock.acquire(blocking=False):
        summary["status"] = "busy"
        summary["message"] = (
            "A campaign is already running. Please wait for it to finish."
        )
        return summary

    confirmed_sent: list[str] = []
    unlogged: list[str] = []
    failed: list[str] = []
    smtp = None

    try:
        try:
            smtp = get_smtp_connection()
            logger.info("[Sender] SMTP connection established")
        except Exception as e:
            logger.error(f"[Sender] could not connect to SMTP: {e}")
            summary["status"] = "auth_error"
            summary["message"] = f"Gmail authentication failed: {e}"
            return summary

        for buyer in batch:
            email = str(buyer.get("email", "") or "").strip().lower()
            if not email:
                continue

            meta = {
                "buyer_name": buyer.get("buyer_name", ""),
                "company_name": buyer.get("company_name", ""),
                "website": buyer.get("website", ""),
                "country": buyer.get("country", ""),
                "product": PRODUCT_NAME,
                "source_platform": buyer.get("source_platform", ""),
                "campaign_subject": subject,
            }

            summary["attempted"] += 1
            try:
                msg = build_email(
                    receiver_email=email,
                    subject=subject,
                    body=body,
                    attachment_path=presentation_path,
                    cc_email=cc_email,
                )
                smtp.send_message(msg)
            except AttachmentError:
                log_send_attempt(
                    email=email,
                    status="failed",
                    error_message="attachment_error",
                    **meta,
                )
                failed.append(email)
                logger.error(f"[Sender] FAILED (attachment) - {email}")
            except smtplib.SMTPRecipientsRefused as e:
                log_send_attempt(
                    email=email,
                    status="failed",
                    error_message=str(e),
                    **meta,
                )
                failed.append(email)
                logger.error(f"[Sender] FAILED (recipient refused) - {email}: {e}")
            except Exception as e:
                log_send_attempt(
                    email=email,
                    status="failed",
                    error_message=str(e),
                    **meta,
                )
                failed.append(email)
                logger.error(f"[Sender] FAILED - {email}: {e}")
            else:
                # Send succeeded. Record it, then only then remove the buyer.
                logged = log_send_attempt(
                    email=email,
                    status="sent",
                    attachment=os.path.basename(presentation_path),
                    **meta,
                )
                if logged:
                    confirmed_sent.append(email)
                    logger.info(f"[Sender] SENT - {email}")
                else:
                    # Do NOT remove from buyers.csv: the log is the source of
                    # truth for who was contacted.
                    unlogged.append(email)
                    logger.error(
                        f"[Sender] SENT but NOT logged - {email}: buyer kept in "
                        f"buyers.csv to avoid losing send history"
                    )

            # Rate limit between recipients within the batch.
            if summary["attempted"] < len(batch):
                time.sleep(random.uniform(MIN_SEND_DELAY, MAX_SEND_DELAY))

    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass
        _campaign_lock.release()

    # ── Only confirmed+logged sends leave buyers.csv ───────────
    remove_sent_buyers(confirmed_sent)

    summary["sent"] = len(confirmed_sent)
    summary["failed"] = len(failed)
    summary["unlogged"] = len(unlogged)
    # Report the buyers.csv row count so this matches the dashboard counter.
    # load_unsent_buyers() also filters WHOIS/infrastructure rows, which would
    # understate the pool.
    summary["remaining"] = len(load_buyers())
    summary["message"] = (
        f"Campaign completed. Sent successfully: {summary['sent']}, "
        f"Failed: {summary['failed']}, Remaining buyers: {summary['remaining']}"
    )
    if unlogged:
        summary["status"] = "log_error"
        summary["message"] += (
            f" | WARNING: {len(unlogged)} email(s) were sent but could NOT be "
            f"written to sent_log.csv; those buyers were kept in buyers.csv"
        )
    return summary
