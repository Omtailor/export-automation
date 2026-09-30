#!/usr/bin/env python3
"""
Product Zone International — buyer discovery & campaign CLI.

Stages:
  1  Search across all configured sources
  2  Extract emails from discovered URLs
  3  Validate emails (syntax → domain → MX → optional SMTP)
  4  Save new buyers (case-insensitive dedup against buyers.csv AND
     exclusion of anything already in sent_log.csv)
  5  Send one campaign batch (max 10 recipients, unless --dry-run)
  6  Print final report

Usage:
  python main.py                 # full pipeline with one campaign batch
  python main.py --dry-run       # full pipeline, no emails sent
  python main.py --search-only   # search, extract, validate, save only
  python main.py --search        # limited search run (same as --search-only)
  python main.py --help
"""

import argparse
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

from config import (
    COMPANY_NAME,
    DEFAULT_BODY,
    DEFAULT_SUBJECT,
    ENABLE_SMTP_VERIFICATION,
    GMAIL_EMAIL,
    PRESENTATION_PATH,
    PRODUCT_NAME,
    SEARCH_QUERIES,
)
from search import run_all_sources
from extraction import extract_emails_from_results
from validation import validate_email_records
from activity_logging import load_buyers, load_sent_log, save_new_buyers
from outreach import run_campaign

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SENT_LOG_CSV = os.path.join(DATA_DIR, "sent_log.csv")


# ──────────────────────────────────────────────────────────────
# Formatting helpers
# ──────────────────────────────────────────────────────────────


def _section(title: str):
    print(f"\n{'═' * 62}")
    print(f"  {title}")
    print(f"{'═' * 62}\n")


def _step(n: int, desc: str):
    print(f"  [{n}] {desc}")


def _info(msg: str):
    print(f"      {msg}")


def _discover(result_limit: int | None = None) -> dict:
    """Search → extract → validate → dedup → exclude sent → save."""
    _step(1, "Searching across all sources…")
    try:
        raw_results = run_all_sources()
    except Exception as e:
        _info(f"ERROR in search stage: {e}")
        raw_results = []
    if result_limit and len(raw_results) > result_limit:
        raw_results = raw_results[:result_limit]
        _info(f"Limited results : {len(raw_results)}")
    _info(f"Raw results       : {len(raw_results)}")

    _step(2, "Extracting emails from results…")
    try:
        extracted_emails = extract_emails_from_results(raw_results)
    except Exception as e:
        _info(f"ERROR in extraction stage: {e}")
        extracted_emails = []
    whois_count = sum(
        1 for e in extracted_emails if e.get("source_platform") == "whois"
    )
    _info(f"Emails extracted : {len(extracted_emails)} ({whois_count} WHOIS-derived)")

    _step(3, "Validating email addresses…")
    try:
        valid_emails = validate_email_records(
            extracted_emails, check_smtp=ENABLE_SMTP_VERIFICATION
        )
    except Exception as e:
        _info(f"ERROR in validation stage: {e}")
        valid_emails = []
    _info(f"Valid emails      : {len(valid_emails)}")
    _info(f"Flagged / invalid : {len(extracted_emails) - len(valid_emails)}")

    _step(4, "Saving new buyers (dedup + sent-history filter)…")
    already_sent = load_sent_log()
    try:
        new_buyers_count = save_new_buyers(valid_emails)
        all_buyers = load_buyers()
    except Exception as e:
        _info(f"ERROR saving buyers: {e}")
        new_buyers_count = 0
        all_buyers = []
    _info(f"Already sent (skipped) : {len(already_sent)}")
    _info(f"New buyers added       : {new_buyers_count}")
    _info(f"Unsent buyers in DB    : {len(all_buyers)}")

    return {
        "raw_results": raw_results,
        "extracted_emails": extracted_emails,
        "valid_emails": valid_emails,
        "whois_count": whois_count,
        "new_buyers_count": new_buyers_count,
        "all_buyers": all_buyers,
    }


def _read_report() -> dict:
    report = {
        "total_attempts": 0,
        "successful_sends": 0,
        "failed_sends": 0,
        "failed_details": [],
    }
    if not os.path.exists(SENT_LOG_CSV):
        return report
    try:
        log_df = pd.read_csv(SENT_LOG_CSV).dropna(how="all")
        if not log_df.empty and "status" in log_df.columns:
            statuses = log_df["status"].fillna("").astype(str).str.strip().str.lower()
            report["total_attempts"] = len(log_df)
            report["successful_sends"] = int((statuses == "sent").sum())
            report["failed_sends"] = int((statuses == "failed").sum())
            keep_cols = [
                c
                for c in ["email", "status", "sent_at", "error_message"]
                if c in log_df.columns
            ]
            report["failed_details"] = (
                log_df[statuses == "failed"][keep_cols].to_dict("records")
            )
    except Exception:
        pass
    return report


# ──────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────


def run_full_pipeline(dry_run: bool = False):
    start_time = datetime.now()

    _section("B2B BUYER OUTREACH — FULL PIPELINE")
    _info(f"Started      : {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    _info(f"Company      : {COMPANY_NAME}")
    _info(f"Product      : {PRODUCT_NAME}")
    _info(f"Queries      : {len(SEARCH_QUERIES)} B2B buyer queries")
    _info(f"Dry run      : {dry_run}")
    _info(f"SMTP verify  : {ENABLE_SMTP_VERIFICATION}")

    if not dry_run and (not PRESENTATION_PATH or not os.path.exists(PRESENTATION_PATH)):
        print(
            f"\n[Pipeline] ERROR: Presentation file not found: {PRESENTATION_PATH}\n"
            "  Set PRESENTATION_PATH in .env and ensure the file exists.\n"
            "  Use --dry-run to run without sending.\n"
        )
        sys.exit(1)

    discovery = _discover()

    # ── Send one campaign batch (or skip) ──────────────────────
    summary = None
    if dry_run:
        _step(5, "Email campaign…")
        _info("DRY RUN — email sending skipped")
        _info(f"Subject     : {DEFAULT_SUBJECT}")
        _info(f"Recipients  : {len(discovery['all_buyers'])} unsent buyer(s) available")
        _info(
            f"Next campaign would contact at most "
            f"{min(10, len(discovery['all_buyers']))} buyer(s)"
        )
        print("\n  ── Email preview (universal, same for every recipient) ──")
        print(DEFAULT_BODY)
    else:
        _step(5, "Sending one campaign batch…")
        summary = run_campaign(DEFAULT_SUBJECT, DEFAULT_BODY)
        _info(f"Status      : {summary['status']}")
        _info(f"Attempted   : {summary['attempted']}")
        _info(f"Sent        : {summary['sent']}")
        _info(f"Failed      : {summary['failed']}")
        _info(f"Remaining   : {summary['remaining']}")

    _step(6, "Generating report…")
    report = _read_report()

    end_time = datetime.now()
    _section("PIPELINE COMPLETION REPORT")
    _info(f"Completed   : {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    _info(f"Duration    : {end_time - start_time}")

    print()
    print("  ── Search & Discovery ─────────────────────────────────")
    _info(f"Queries executed      : {len(SEARCH_QUERIES)}")
    _info(f"Raw search results    : {len(discovery['raw_results'])}")
    _info(f"Emails extracted      : {len(discovery['extracted_emails'])}")
    _info(f"Valid after validation: {len(discovery['valid_emails'])}")

    print()
    print("  ── Buyers ───────────────────────────────────────────")
    _info(f"New buyers this run   : {discovery['new_buyers_count']}")
    _info(f"Unsent buyers         : {len(discovery['all_buyers'])}")

    print()
    print("  ── Outreach ──────────────────────────────────────────")
    _info(f"Total log entries     : {report['total_attempts']}")
    _info(f"Successfully sent     : {report['successful_sends']}")
    _info(f"Failed                : {report['failed_sends']}")

    if report["failed_details"]:
        print()
        print("  ── Failed recipients ─────────────────────────────────")
        for row in report["failed_details"][:20]:
            err = row.get("error_message", "")
            _info(f"  {row.get('email','')}{' — ' + err if err else ''}")
        if len(report["failed_details"]) > 20:
            _info(f"  … and {len(report['failed_details']) - 20} more (see {SENT_LOG_CSV})")

    _section("DONE")


def run_search_only(result_limit: int | None = None):
    """Run search through validated buyer storage. Never sends email."""
    start_time = datetime.now()

    _section("B2B BUYER OUTREACH — SEARCH ONLY")
    _info(f"Started     : {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    _info(f"Product     : {PRODUCT_NAME}")
    _info(f"Queries     : {len(SEARCH_QUERIES)} B2B buyer queries")
    _info(f"SMTP verify : {ENABLE_SMTP_VERIFICATION}")

    _discover(result_limit=result_limit)

    _section("SEARCH-ONLY COMPLETE — no emails were sent")


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Product Zone International — B2B buyer discovery and outreach",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py            # full pipeline with one campaign batch\n"
            "  python main.py --dry-run  # pipeline without sending emails\n"
            "  python main.py --search   # search only, no emails sent\n"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without sending actual emails",
    )
    parser.add_argument(
        "--search-only",
        action="store_true",
        help="Search, extract, validate, and save buyers without sending",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="Limited search/extraction/validation run (no emails sent)",
    )
    args = parser.parse_args()

    try:
        if args.search_only or args.search:
            run_search_only(result_limit=50 if args.search else None)
        else:
            run_full_pipeline(dry_run=args.dry_run)
    except KeyboardInterrupt:
        print("\n[Pipeline] interrupted by user.")
        sys.exit(0)
    except Exception as e:
        _section("PIPELINE ERROR")
        print(f"  {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
