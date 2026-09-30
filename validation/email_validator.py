"""
Email validation module.

Validation pipeline (in order):
  1. Syntax check
  2. No image-extension suffix
  3. Domain length <= 63 chars
  4. Not a disposable domain
  5. Not a hard-blocked system prefix (noreply / no-reply / donotreply / mailer-daemon)
  6. MX record exists (requires dnspython)
  7. Optional SMTP verification (ENABLE_SMTP_VERIFICATION=true)

Key design decisions:
- 'info@', 'sales@', 'admin@', 'support@', 'wholesale@', 'export@',
  'purchasing@', 'procurement@', 'orders@' are NOT blocked — these are
  legitimate business contacts.  Only clear system/noreply addresses are
  hard-blocked.
- SMTP result is three-valued: verified / rejected / unknown.  Only
  'rejected' (550) discards the email; unknown/timeout keeps it.
- Deduplication is case-insensitive (john@Co.com == JOHN@co.com).
- WHOIS-sourced emails (tagged source_platform='whois') pass the same
  checks but are noted in the flagged log when they fail.
"""

import re
import os
import sys
import logging
import smtplib
import socket
import pandas as pd
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Regex ──────────────────────────────────────────────────────
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# ── Image-extension guard ──────────────────────────────────────
_IMAGE_EXTS = {".png", ".jpg", ".gif", ".svg", ".jpeg", ".webp", ".ico"}

# ── Hard-blocked local-part prefixes (exact match on normalised local part) ──
# ONLY clear system / no-reply addresses.
# DO NOT add: info, sales, admin, support, orders, wholesale, export,
#             purchasing, procurement — those are valid business contacts.
_HARD_BLOCKED_EXACT = {
    "abuse",
    "domain",
    "domains",
    "hostmaster",
    "hostadmin",
    "privacy",
    "whois",
    "registrar",
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "mailer-daemon",
    "mailer",
    "postmaster",  # system address — not a sales contact
    "bounce",
    "bounces",
    "notifications",
    "unsubscribe",
    "do_not_reply",
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

# ── Disposable-domain list ─────────────────────────────────────
DISPOSABLE_DOMAINS: set[str] = set()
_disposable_file = os.path.join(os.path.dirname(__file__), "disposable_domains.txt")
if os.path.exists(_disposable_file):
    with open(_disposable_file, "r", encoding="utf-8", errors="ignore") as _f:
        DISPOSABLE_DOMAINS = {line.strip().lower() for line in _f if line.strip()}
    logger.info(f"[Validator] loaded {len(DISPOSABLE_DOMAINS)} disposable domains")
else:
    # Minimal fallback — file should always be present
    DISPOSABLE_DOMAINS = {
        "mailinator.com",
        "tempmail.com",
        "guerrillamail.com",
        "throwaway.email",
        "yopmail.com",
        "trashmail.com",
        "sharklasers.com",
        "spam4.me",
        "dispostable.com",
    }
    logger.warning("[Validator] disposable_domains.txt not found — using fallback list")


# ──────────────────────────────────────────────────────────────
# Individual validation steps
# ──────────────────────────────────────────────────────────────


def validate_email_syntax(email: str) -> bool:
    """Basic regex syntax check."""
    return bool(EMAIL_REGEX.fullmatch(email.strip()))


def validate_no_image_extension(email: str) -> bool:
    """Reject emails that look like filename artefacts (e.g. image@foo.png)."""
    return not any(email.lower().endswith(ext) for ext in _IMAGE_EXTS)


def validate_domain_length(email: str) -> bool:
    """Validate each DNS label and the full domain length."""
    domain = email.split("@", 1)[1] if "@" in email else ""
    labels = domain.rstrip(".").split(".")
    return (
        bool(labels)
        and len(domain) <= 253
        and all(1 <= len(label) <= 63 for label in labels)
    )


def validate_not_disposable(email: str) -> bool:
    """Reject known disposable / throwaway domains."""
    domain = email.split("@", 1)[1].lower() if "@" in email else ""
    return domain not in DISPOSABLE_DOMAINS


def validate_not_blocked_prefix(email: str) -> bool:
    """
    Reject only clear system / no-reply local parts (exact match).
    Does NOT reject info@, sales@, admin@, support@, orders@, etc.
    """
    local = email.split("@", 1)[0].lower() if "@" in email else email.lower()
    return local not in _HARD_BLOCKED_EXACT


def validate_not_placeholder(email: str) -> bool:
    """Reject only clearly artificial/test addresses."""
    local, _, domain = email.strip().lower().partition("@")
    return domain not in _PLACEHOLDER_DOMAINS and local not in _PLACEHOLDER_LOCAL_PARTS


def validate_mx_record(email: str) -> bool:
    """
    Check that the email's domain has at least one MX record.
    Returns True (skip check) if dnspython is not installed.
    """
    try:
        import dns.resolver

        domain = email.split("@", 1)[1] if "@" in email else ""
        if not domain:
            return False
        try:
            mx_records = dns.resolver.resolve(domain, "MX")
            return len(mx_records) > 0
        except (
            dns.resolver.NXDOMAIN,
            dns.resolver.NoAnswer,
            dns.resolver.NoNameservers,
        ):
            logger.debug(f"[Validator] no MX records for {domain}")
            return False
        except Exception as e:
            logger.debug(f"[Validator] MX lookup error for {domain}: {e}")
            return False

    except ImportError:
        logger.warning("[Validator] dnspython not installed — skipping MX check")
        return True  # don't discard email just because library is missing


def validate_smtp_mailbox(email: str, timeout: int = 8) -> str:
    """
    Attempt an SMTP RCPT TO handshake WITHOUT sending any message.

    Returns one of:
      'verified'  — server returned 250 (mailbox exists)
      'rejected'  — server returned 550 (mailbox does not exist)
      'unknown'   — any other response or error (keep the email)
      'timeout'   — connection timed out (keep the email)
    """
    try:
        import dns.resolver

        domain = email.split("@", 1)[1] if "@" in email else ""
        if not domain:
            return "unknown"

        try:
            mx_records = dns.resolver.resolve(domain, "MX")
            mx_host = str(
                sorted(mx_records, key=lambda r: r.preference)[0].exchange
            ).rstrip(".")
        except Exception as e:
            logger.debug(f"[SMTP] cannot get MX for {domain}: {e}")
            return "unknown"

        try:
            with smtplib.SMTP(timeout=timeout) as srv:
                srv.connect(mx_host)
                srv.helo(srv.local_hostname or "verify.example.com")
                srv.mail("verify@example.com")
                code, _ = srv.rcpt(email)
                if code == 250:
                    return "verified"
                elif code == 550:
                    return "rejected"
                else:
                    return "unknown"
        except socket.timeout:
            return "timeout"
        except Exception as e:
            logger.debug(f"[SMTP] connection error for {email}: {e}")
            return "unknown"

    except ImportError:
        logger.warning("[Validator] dnspython not installed — skipping SMTP check")
        return "unknown"


# ──────────────────────────────────────────────────────────────
# WHOIS helper (used by other modules; kept here for backward compat)
# ──────────────────────────────────────────────────────────────


def extract_emails_from_whois(domain: str) -> list[str]:
    """
    Extract emails from WHOIS data.

    These should be treated as lower-confidence contacts.
    Filters out obvious registrar / privacy-service addresses.
    """
    try:
        import whois as _whois
    except ImportError:
        logger.warning("[Validator] python-whois not installed — skipping WHOIS lookup")
        return []

    # Strip protocol/path
    domain = re.sub(r"^https?://", "", domain).split("/")[0].replace("www.", "")

    try:
        w = _whois.whois(domain)
        raw: list[str] = []
        for field in [
            w.emails,
            getattr(w, "admin_email", None),
            getattr(w, "tech_email", None),
        ]:
            if not field:
                continue
            if isinstance(field, list):
                raw.extend([str(e) for e in field if e])
            else:
                raw.append(str(field))

        noise = {"privacy", "protection", "proxy", "whoisguard", "redacted", "abuse"}
        cleaned = [
            e.strip().lower()
            for e in raw
            if e
            and EMAIL_REGEX.match(e.strip())
            and not any(kw in e.lower() for kw in noise)
        ]
        return list(dict.fromkeys(cleaned))

    except Exception as e:
        logger.debug(f"[WHOIS] lookup failed for {domain}: {e}")
        return []


# ──────────────────────────────────────────────────────────────
# Per-record validation
# ──────────────────────────────────────────────────────────────


def validate_email_record(
    record: dict, check_smtp: bool = False
) -> tuple[bool, list[str]]:
    """
    Run all validation checks on a single email record.

    Returns (is_valid, list_of_failed_check_names).
    """
    email = record.get("email", "").strip().lower()

    checks = [
        ("syntax", validate_email_syntax(email)),
        ("no_image_extension", validate_no_image_extension(email)),
        ("domain_length", validate_domain_length(email)),
        ("not_disposable", validate_not_disposable(email)),
        ("not_blocked_prefix", validate_not_blocked_prefix(email)),
        ("not_placeholder", validate_not_placeholder(email)),
        ("mx_record", validate_mx_record(email)),
    ]

    if check_smtp:
        smtp_result = validate_smtp_mailbox(email)
        # Only hard-fail on explicit 'rejected'; unknown/timeout keeps the email
        checks.append(("smtp_mailbox", smtp_result != "rejected"))
        record["smtp_status"] = smtp_result  # store result in record for reporting

    failed = [name for name, passed in checks if not passed]
    return (len(failed) == 0), failed


# ──────────────────────────────────────────────────────────────
# Deduplication
# ──────────────────────────────────────────────────────────────


def deduplicate_records(records: list[dict]) -> list[dict]:
    """
    Case-insensitive email deduplication.
    First occurrence wins (preserves the richest metadata if sorted first).
    """
    seen: set[str] = set()
    unique: list[dict] = []
    for rec in records:
        key = rec.get("email", "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            rec["email"] = key  # normalise to lowercase
            unique.append(rec)
    return unique


# ──────────────────────────────────────────────────────────────
# Flagged-email writer
# ──────────────────────────────────────────────────────────────


def write_flagged_emails(
    flagged_records: list[tuple[dict, list[str]]],
    output_path: str = None,
):
    """Append flagged records (with reasons) to CSV for manual review."""
    if output_path is None:
        output_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "flagged_emails.csv",
        )
    dir_path = os.path.dirname(output_path)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)

    rows = []
    for record, reasons in flagged_records:
        row = record.copy()
        row["flagged_reason"] = ", ".join(reasons)
        row["flagged_date"] = datetime.now(timezone.utc).isoformat()
        rows.append(row)

    if not rows:
        return

    df = pd.DataFrame(rows)
    try:
        if os.path.exists(output_path):
            df.to_csv(output_path, mode="a", header=False, index=False)
        else:
            df.to_csv(output_path, mode="w", header=True, index=False)
        logger.info(
            f"[Validator] {len(rows)} flagged record(s) written to {output_path}"
        )
    except Exception as e:
        logger.error(f"[Validator] could not write flagged emails: {e}")


# ──────────────────────────────────────────────────────────────
# Public entry points
# ──────────────────────────────────────────────────────────────


def validate_email_records(
    raw_records: list[dict],
    flagged_output_path: str = None,
    check_smtp: bool = False,
) -> list[dict]:
    """
    Validate and deduplicate a list of email records.

    Steps:
      1. Case-insensitive deduplication (within the incoming batch)
      2. Per-record validation checks
      3. Write flagged records to CSV for review
      4. Return only records that passed all checks

    Args:
        raw_records:         List of buyer dicts with at least an 'email' key.
        flagged_output_path: Where to write rejected records.
        check_smtp:          Whether to attempt SMTP RCPT TO verification.

    Returns:
        List of validated (passed) records with normalised lowercase emails.
    """
    logger.info(f"[Validator] starting validation of {len(raw_records)} records")
    logger.info(
        f"[Validator] SMTP verification: {'ENABLED' if check_smtp else 'DISABLED'}"
    )
    logger.info(f"[Validator] disposable domains loaded: {len(DISPOSABLE_DOMAINS)}")

    # Step 1 — dedup incoming batch
    deduped = deduplicate_records(raw_records)
    logger.info(
        f"[Validator] after dedup: {len(deduped)} unique "
        f"(removed {len(raw_records) - len(deduped)} duplicates)"
    )

    validated: list[dict] = []
    flagged: list[tuple[dict, list[str]]] = []

    # Counters for reporting
    smtp_stats = {"verified": 0, "rejected": 0, "unknown": 0, "timeout": 0}

    for record in deduped:
        is_valid, failed_checks = validate_email_record(record, check_smtp=check_smtp)

        if check_smtp:
            smtp_status = record.get("smtp_status", "unknown")
            smtp_stats[smtp_status] = smtp_stats.get(smtp_status, 0) + 1

        if is_valid:
            validated.append(record)
        else:
            flagged.append((record, failed_checks))
            logger.info(
                f"[Validator] flagged {record.get('email')} — {', '.join(failed_checks)}"
            )

    if flagged:
        write_flagged_emails(flagged, flagged_output_path)

    # Summary log
    logger.info("[Validator] validation complete:")
    logger.info(f"  Total input   : {len(raw_records)}")
    logger.info(f"  After dedup   : {len(deduped)}")
    logger.info(f"  Passed        : {len(validated)}")
    logger.info(f"  Flagged       : {len(flagged)}")
    if check_smtp:
        logger.info(f"  SMTP verified : {smtp_stats['verified']}")
        logger.info(f"  SMTP rejected : {smtp_stats['rejected']}")
        logger.info(f"  SMTP unknown  : {smtp_stats['unknown']}")
        logger.info(f"  SMTP timeout  : {smtp_stats['timeout']}")

    return validated


def run_validation(raw_records: list[dict], check_smtp: bool = False) -> list[dict]:
    """Alias kept for backward compatibility."""
    return validate_email_records(raw_records, check_smtp=check_smtp)
