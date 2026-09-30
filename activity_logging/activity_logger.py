"""
Activity logger — manages buyers.csv and sent_log.csv.

Responsibilities:
- buyers.csv  : validated, unsent B2B buyers discovered by the search pipeline
- sent_log.csv: every send attempt, with success/failure status

Key guarantees:
- save_new_buyers() dedupes case-insensitively against buyers.csv AND
  sent_log.csv, so an already-sent address is never re-added.
- remove_sent_buyers() removes ONLY successfully-sent addresses from
  buyers.csv, so failed sends remain available for a later campaign.
- All CSV rewrites are atomic (temp file + os.replace) so an interrupted
  write cannot corrupt the data files.
"""

import csv
import pandas as pd
import os
import sys
from datetime import datetime, timezone
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Resolved against the project root so the CSVs are found regardless of the
# working directory the app was started from.
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
BUYERS_CSV = os.path.join(DATA_DIR, "buyers.csv")
SENT_LOG_CSV = os.path.join(DATA_DIR, "sent_log.csv")

BUYERS_COLUMNS = [
    "buyer_name",
    "company_name",
    "email",
    "website",
    "country",
    "source_platform",
    "discovered_at",
]

SENT_LOG_COLUMNS = [
    "email",
    "status",
    "sent_at",
    "campaign_subject",
    "buyer_name",
    "company_name",
    "website",
    "country",
    "product",
    "source_platform",
    "attachment",
    "error_message",
]


def normalise_email(email: str) -> str:
    """Canonical comparison form for an email address (strip + lowercase)."""
    return str(email or "").strip().lower()


def ensure_data_directory():
    os.makedirs(DATA_DIR, exist_ok=True)


def _atomic_write(df: pd.DataFrame, path: str) -> None:
    """Write a dataframe to CSV via a temp file so readers never see a partial file."""
    tmp_path = f"{path}.tmp"
    df.to_csv(tmp_path, index=False)
    os.replace(tmp_path, path)


def _write_headers(path: str, columns: list[str]) -> bool:
    """Create (or re-create) a CSV containing only the header row."""
    try:
        ensure_data_directory()
        _atomic_write(pd.DataFrame(columns=columns), path)
        return True
    except OSError as e:
        logger.error(f"[Logger] could not initialise {path}: {e}")
        return False


def read_csv_with_headers(path: str, columns: list[str]) -> pd.DataFrame:
    """
    Read a CSV that may be missing, empty (0 bytes), or header-only.

    In every case a DataFrame with the expected columns is returned, so callers
    never hit pandas' "No columns to parse from file" EmptyDataError. A file
    that exists but has no usable header is (re)initialised with the correct
    headers. Existing rows are always preserved.
    """
    if not os.path.exists(path):
        _write_headers(path, columns)
        return pd.DataFrame(columns=columns)

    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        # 0-byte file: initialise it instead of failing.
        logger.warning(f"[Logger] {path} was empty - initialising with headers")
        _write_headers(path, columns)
        return pd.DataFrame(columns=columns)
    except pd.errors.ParserError:
        df = _repair_broken_csv(path, columns)
        if df is None:
            return pd.DataFrame(columns=columns)

    if "Unnamed: 0" in df.columns and len(df.columns) == len(columns) + 1:
        df = df.drop(columns=["Unnamed: 0"])

    if not set(columns).issubset(set(df.columns)):
        # Schema drift: migrate while preserving any rows that did parse.
        df = df.reindex(columns=columns, fill_value="")
        _atomic_write(df, path)

    return df.dropna(how="all")


def _repair_broken_csv(path: str, columns: list[str]) -> pd.DataFrame | None:
    """Rebuild a malformed CSV from raw rows, preserving send history."""
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as csv_file:
            rows = list(csv.reader(csv_file))
    except OSError:
        return None
    if not rows:
        _write_headers(path, columns)
        return pd.DataFrame(columns=columns)

    header, data_rows = rows[0], rows[1:]
    if not set(columns).issubset(set(header)):
        _write_headers(path, columns)
        return pd.DataFrame(columns=columns)

    normalized = []
    for row in data_rows:
        if len(row) > len(header):
            row = row[: len(header) - 1] + [
                row[len(header) - 1] + ", " + ", ".join(row[len(header) :])
            ]
        normalized.append(row + [""] * (len(header) - len(row)))
    df = pd.DataFrame(normalized, columns=header)
    _atomic_write(df, path)
    logger.warning(f"[Logger] repaired malformed {path}, kept {len(df)} row(s)")
    return df


def _repair_buyer_row(fields: list[str]) -> list[str]:
    if len(fields) > len(BUYERS_COLUMNS):
        # Preserve the original buyer columns when legacy rows contain
        # extra fields appended after them.
        return fields[: len(BUYERS_COLUMNS)]
    return fields


def _ensure_buyer_schema() -> None:
    """Migrate buyers.csv to the current schema while preserving buyer data."""
    if not os.path.exists(BUYERS_CSV):
        return
    try:
        df = pd.read_csv(
            BUYERS_CSV, engine="python", on_bad_lines=_repair_buyer_row
        ).dropna(how="all")
        original_count = len(df)
        if "email" in df.columns:
            df = df[df["email"].fillna("").astype(str).str.strip() != ""]
        if list(df.columns) != BUYERS_COLUMNS or len(df) != original_count:
            df = df.reindex(columns=BUYERS_COLUMNS, fill_value="")
            _atomic_write(df, BUYERS_CSV)
            logger.info(f"[Logger] migrated buyers.csv schema to {BUYERS_COLUMNS}")
    except (pd.errors.EmptyDataError, OSError):
        return


def _ensure_sent_log_schema() -> None:
    """Create sent_log.csv with headers if missing/empty; migrate the schema if stale."""
    read_csv_with_headers(SENT_LOG_CSV, SENT_LOG_COLUMNS)


def _read_buyers_df() -> pd.DataFrame:
    if not os.path.exists(BUYERS_CSV):
        return pd.DataFrame(columns=BUYERS_COLUMNS)
    _ensure_buyer_schema()
    try:
        df = pd.read_csv(BUYERS_CSV, engine="python", on_bad_lines=_repair_buyer_row)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=BUYERS_COLUMNS)
    df = df.dropna(how="all")
    df = df.reindex(columns=BUYERS_COLUMNS, fill_value="")
    if "email" in df.columns:
        df = df[df["email"].fillna("").astype(str).str.strip() != ""]
    return df


# ──────────────────────────────────────────────────────────────
# Buyers CSV
# ──────────────────────────────────────────────────────────────


def load_buyers() -> list[dict]:
    """Load all buyers from buyers.csv.  Returns [] on missing / empty file."""
    ensure_data_directory()
    if not os.path.exists(BUYERS_CSV):
        logger.info("[Logger] buyers.csv not found — returning empty list")
        return []
    try:
        df = _read_buyers_df()
        if df.empty:
            return []
        buyers = df.to_dict("records")
        logger.info(f"[Logger] loaded {len(buyers)} buyers from {BUYERS_CSV}")
        return buyers
    except Exception as e:
        logger.error(f"[Logger] error loading buyers: {e}")
        return []


def save_new_buyers(new_records: list[dict]) -> int:
    """
    Append new buyer records to buyers.csv.

    Skips any address that is already in buyers.csv, already appears in this
    batch, or was already successfully sent (sent_log.csv).

    Returns the number of records actually written.
    """
    ensure_data_directory()

    existing_buyers = load_buyers()
    existing_emails: set[str] = {
        normalise_email(b.get("email", "")) for b in existing_buyers if b.get("email")
    }
    sent_emails = load_sent_log()
    if sent_emails:
        logger.info(
            f"[Logger] excluding {len(sent_emails)} previously-sent address(es) "
            f"from new buyers"
        )

    seen_in_batch: set[str] = set()
    unique_new: list[dict] = []
    for record in new_records:
        norm = normalise_email(record.get("email", ""))
        if not norm:
            continue
        if norm in existing_emails or norm in seen_in_batch or norm in sent_emails:
            continue
        seen_in_batch.add(norm)
        new_record = {
            key: record.get(key, "")
            for key in BUYERS_COLUMNS
            if key != "discovered_at"
        }
        new_record["email"] = norm
        new_record["discovered_at"] = datetime.now(timezone.utc).isoformat()
        unique_new.append(new_record)

    if not unique_new:
        logger.info("[Logger] no new unique buyers to save")
        return 0

    new_df = pd.DataFrame(unique_new).reindex(columns=BUYERS_COLUMNS, fill_value="")
    try:
        if os.path.exists(BUYERS_CSV):
            existing_df = _read_buyers_df()
            combined = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            combined = new_df
        _atomic_write(combined, BUYERS_CSV)
        logger.info(f"[Logger] saved {len(unique_new)} new buyers to {BUYERS_CSV}")
        return len(unique_new)
    except Exception as e:
        logger.error(f"[Logger] error saving buyers: {e}")
        return 0


def remove_sent_buyers(sent_emails: list[str]) -> int:
    """
    Remove the given (successfully sent) addresses from buyers.csv.

    Comparison is case-insensitive.  Any address that was not actually sent
    stays in buyers.csv.  Returns the number of removed rows.
    """
    targets = {normalise_email(e) for e in sent_emails if e}
    if not targets:
        return 0
    ensure_data_directory()
    if not os.path.exists(BUYERS_CSV):
        return 0
    try:
        df = _read_buyers_df()
        if df.empty:
            return 0
        normalised = df["email"].map(normalise_email)
        before = len(df)
        remaining = df[~normalised.isin(targets)].reset_index(drop=True)
        removed = before - len(remaining)
        if removed:
            _atomic_write(remaining, BUYERS_CSV)
            logger.info(f"[Logger] removed {removed} sent buyer(s) from {BUYERS_CSV}")
        return removed
    except Exception as e:
        logger.error(f"[Logger] error removing sent buyers: {e}")
        return 0


# ──────────────────────────────────────────────────────────────
# Sent log
# ──────────────────────────────────────────────────────────────


def load_sent_log() -> set[str]:
    """
    Return the set of emails that were SUCCESSFULLY sent (status == 'sent').
    Case-insensitive.

    Emails with status 'failed' are NOT included — they remain eligible for
    retry in a future run.
    """
    ensure_data_directory()
    df = read_csv_with_headers(SENT_LOG_CSV, SENT_LOG_COLUMNS)
    if df.empty or "status" not in df.columns or "email" not in df.columns:
        return set()
    statuses = df["status"].fillna("").astype(str).str.strip().str.lower()
    sent = {normalise_email(e) for e in df.loc[statuses == "sent", "email"].dropna()}
    sent.discard("")
    logger.info(f"[Logger] {len(sent)} successfully-sent email(s) in log")
    return sent


def log_send_attempt(
    email: str,
    status: str,
    campaign_subject: str,
    buyer_name: str = "",
    company_name: str = "",
    country: str = "",
    product: str = "",
    source_platform: str = "",
    attachment: str = "",
    error_message: str = "",
    website: str = "",
) -> bool:
    """
    Append one row to sent_log.csv and confirm it landed on disk.

    status should be one of:
      'sent'    — delivered successfully
      'failed'  — temporary / unknown error (buyer stays in buyers.csv)

    Returns True only when the row is verifiably present in the file after the
    write. Callers must NOT treat a send as logged when this returns False.
    """
    ensure_data_directory()

    entry = {
        "email": normalise_email(email),
        "status": status,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "campaign_subject": campaign_subject,
        "buyer_name": buyer_name,
        "company_name": company_name,
        "website": website,
        "country": country,
        "product": product,
        "source_platform": source_platform,
        "attachment": attachment,
        "error_message": error_message,
    }

    try:
        existing_df = read_csv_with_headers(SENT_LOG_CSV, SENT_LOG_COLUMNS)
        new_df = pd.DataFrame([entry]).reindex(columns=SENT_LOG_COLUMNS, fill_value="")
        combined = (
            pd.concat([existing_df, new_df], ignore_index=True)
            if not existing_df.empty
            else new_df
        )
        _atomic_write(combined, SENT_LOG_CSV)
    except Exception as e:
        logger.error(f"[Logger] error writing sent log: {e}")
        return False

    # Verify the row is really on disk before reporting success.
    try:
        verify = read_csv_with_headers(SENT_LOG_CSV, SENT_LOG_COLUMNS)
        if verify.empty or "email" not in verify.columns:
            raise ValueError("sent log unreadable after write")
        stored = {normalise_email(e) for e in verify["email"].dropna()}
        if normalise_email(email) not in stored:
            raise ValueError("entry missing after write")
    except Exception as e:
        logger.error(f"[Logger] sent log write NOT confirmed for {email}: {e}")
        return False

    logger.info(f"[Logger] logged {status} for {email}")
    return True


def get_unsent_buyers() -> list[dict]:
    """Return buyers that have not been successfully emailed yet."""
    all_buyers = load_buyers()
    sent_emails = load_sent_log()
    unsent = [
        b for b in all_buyers if normalise_email(b.get("email", "")) not in sent_emails
    ]
    logger.info(
        f"[Logger] {len(unsent)} unsent buyers (of {len(all_buyers)} total, "
        f"{len(sent_emails)} sent)"
    )
    return unsent


def get_all_buyers() -> list[dict]:
    """Return all buyers (alias kept for backward compatibility)."""
    return load_buyers()
