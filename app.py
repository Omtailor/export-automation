import argparse
import os
import sys

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, send_file, url_for

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from activity_logging import load_buyers, load_sent_log, save_new_buyers
from activity_logging.activity_logger import BUYERS_CSV, SENT_LOG_CSV
from config import (
    CAMPAIGN_BATCH_SIZE,
    DEFAULT_BODY,
    DEFAULT_SUBJECT,
    ENABLE_SMTP_VERIFICATION,
    GMAIL_EMAIL,
    PRESENTATION_PATH,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")


def _read_csv_safely(path: str) -> pd.DataFrame:
    """Read current CSV contents without letting malformed data break the UI."""
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, on_bad_lines="skip").dropna(how="all")
    except (pd.errors.EmptyDataError, pd.errors.ParserError, OSError, ValueError):
        return pd.DataFrame()


def _current_statistics() -> tuple[int, int, int, int]:
    """Return (available_buyers, total_sent, total_failed, remaining)."""
    buyers_df = _read_csv_safely(BUYERS_CSV)
    sent_log_df = _read_csv_safely(SENT_LOG_CSV)

    total_buyers = len(buyers_df)
    if sent_log_df.empty or "status" not in sent_log_df.columns:
        return total_buyers, 0, 0, total_buyers

    statuses = sent_log_df["status"].fillna("").astype(str).str.strip().str.lower()
    total_sent = int((statuses == "sent").sum())
    total_failed = int((statuses == "failed").sum())
    return total_buyers, total_sent, total_failed, total_buyers


# ──────────────────────────────────────────────────────────────
# Search pipeline (shared by the dashboard and the CLI)
# ──────────────────────────────────────────────────────────────


def run_buyer_search() -> dict:
    """
    Search → extract → validate → deduplicate → exclude sent → save.

    Never sends email.
    """
    from search import run_all_sources
    from extraction import extract_emails_from_results
    from validation import validate_email_records

    results = run_all_sources()
    extracted = extract_emails_from_results(results)
    valid = validate_email_records(extracted, check_smtp=ENABLE_SMTP_VERIFICATION)
    already_sent = load_sent_log()
    new_buyers = save_new_buyers(valid)
    all_buyers = load_buyers()

    return {
        "raw_results": len(results),
        "extracted": len(extracted),
        "valid": len(valid),
        "new_buyers": new_buyers,
        "total_buyers": len(all_buyers),
        "flagged_count": len(extracted) - len(valid),
        "previously_sent": len(already_sent),
    }


# ──────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────


@app.route("/")
def dashboard():
    """Dashboard showing overview statistics."""
    try:
        total_buyers, total_sent, total_failed, remaining = _current_statistics()

        return render_template(
            "dashboard.html",
            total_buyers=total_buyers,
            total_sent=total_sent,
            total_failed=total_failed,
            remaining=remaining,
            batch_size=CAMPAIGN_BATCH_SIZE,
            sender_email=GMAIL_EMAIL,
            presentation_ok=bool(PRESENTATION_PATH)
            and os.path.exists(PRESENTATION_PATH),
        )
    except Exception as e:
        flash(f"Error loading dashboard: {str(e)}", "error")
        return render_template(
            "dashboard.html",
            total_buyers=0,
            total_sent=0,
            total_failed=0,
            remaining=0,
            batch_size=CAMPAIGN_BATCH_SIZE,
            sender_email=GMAIL_EMAIL,
            presentation_ok=False,
        )


@app.route("/find-leads", methods=["POST"])
def find_leads():
    """Run the search + extraction + validation pipeline. Sends no email."""
    try:
        result = run_buyer_search()
        return render_template("leads_result.html", **result)
    except Exception as e:
        flash(f"Error during lead generation: {str(e)}", "error")
        return redirect(url_for("dashboard"))


@app.route("/send", methods=["GET"])
def send_form():
    """Show the campaign form with the universal default content."""
    try:
        _, total_sent, total_failed, remaining = _current_statistics()
    except Exception:
        total_sent = total_failed = remaining = 0

    return render_template(
        "send_form.html",
        default_subject=DEFAULT_SUBJECT,
        default_body=DEFAULT_BODY,
        batch_size=CAMPAIGN_BATCH_SIZE,
        remaining=remaining,
        total_sent=total_sent,
        total_failed=total_failed,
    )


@app.route("/send", methods=["POST"])
def send_campaign():
    """Send at most CAMPAIGN_BATCH_SIZE emails to the next unsent buyers."""
    try:
        from outreach import run_campaign

        subject = request.form.get("subject", "").strip() or DEFAULT_SUBJECT
        body = request.form.get("body", "").strip() or DEFAULT_BODY

        summary = run_campaign(subject, body)

        if summary["status"] == "no_buyers":
            flash(summary["message"], "info")
            return redirect(url_for("dashboard"))
        if summary["status"] == "busy":
            flash(summary["message"], "error")
            return redirect(url_for("send_form"))
        if summary["status"] in ("no_attachment", "auth_error"):
            flash(summary["message"], "error")
            return redirect(url_for("send_form"))

        category = "success"
        if summary["status"] == "log_error" or summary.get("unlogged"):
            category = "error"
        elif summary["failed"]:
            category = "info"

        flash(
            f"Campaign completed. Sent successfully: {summary['sent']} | "
            f"Failed: {summary['failed']} | "
            f"Remaining buyers: {summary['remaining']}",
            category,
        )
        if summary.get("unlogged"):
            flash(
                f"WARNING: {summary['unlogged']} email(s) were sent but could not "
                f"be written to sent_log.csv. Those buyers were kept in "
                f"buyers.csv — check data/sent_log.csv before the next campaign.",
                "error",
            )
        return redirect(url_for("report"))

    except Exception as e:
        flash(f"Error during email campaign: {str(e)}", "error")
        return redirect(url_for("send_form"))


@app.route("/report")
def report():
    """Show campaign report."""
    try:
        _, total_sent, total_failed, remaining = _current_statistics()

        sent_log_df = _read_csv_safely(SENT_LOG_CSV)
        if sent_log_df.empty or "status" not in sent_log_df.columns:
            return render_template(
                "report.html",
                total_attempts=0,
                successful_sends=0,
                failed_sends=0,
                failed_emails=[],
                total_buyers=remaining,
                remaining=remaining,
                sent_log_count=0,
                batch_size=CAMPAIGN_BATCH_SIZE,
            )

        statuses = sent_log_df["status"].fillna("").astype(str).str.strip().str.lower()
        total_attempts = len(sent_log_df)
        successful_sends = int((statuses == "sent").sum())
        failed_sends = int((statuses == "failed").sum())

        fail_cols = [
            c
            for c in ["email", "status", "sent_at", "campaign_subject", "error_message"]
            if c in sent_log_df.columns
        ]
        failed_emails = sent_log_df[statuses == "failed"][fail_cols].to_dict("records")

        return render_template(
            "report.html",
            total_attempts=total_attempts,
            successful_sends=successful_sends,
            failed_sends=failed_sends,
            failed_emails=failed_emails,
            total_buyers=remaining,
            remaining=remaining,
            sent_log_count=total_attempts,
            batch_size=CAMPAIGN_BATCH_SIZE,
        )

    except Exception as e:
        flash(f"Error loading report: {str(e)}", "error")
        return render_template(
            "report.html",
            total_attempts=0,
            successful_sends=0,
            failed_sends=0,
            failed_emails=[],
            total_buyers=0,
            remaining=0,
            batch_size=CAMPAIGN_BATCH_SIZE,
        )


@app.route("/download-report")
def download_report():
    """Download sent log as CSV file."""
    if not os.path.exists(SENT_LOG_CSV):
        flash("No report data available to download", "error")
        return redirect(url_for("report"))

    try:
        return send_file(
            SENT_LOG_CSV, as_attachment=True, download_name="campaign_report.csv"
        )
    except Exception as e:
        flash(f"Error downloading report: {str(e)}", "error")
        return redirect(url_for("report"))


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────


def _cli_search() -> int:
    """`python app.py --search` — discovery only, never sends email."""
    print("=== Buyer Search (no emails will be sent) ===")
    try:
        result = run_buyer_search()
    except Exception as e:
        print(f"Search failed: {e}")
        return 1

    print(f"Raw search results      : {result['raw_results']}")
    print(f"Emails extracted        : {result['extracted']}")
    print(f"Valid after validation  : {result['valid']}")
    print(f"Flagged / rejected      : {result['flagged_count']}")
    print(f"Previously sent (skip)  : {result['previously_sent']}")
    print(f"New buyers added        : {result['new_buyers']}")
    print(f"Total unsent buyers     : {result['total_buyers']}")
    print("\nSearch complete. No emails were sent.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Product Zone International — B2B buyer discovery and outreach"
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="Run buyer search, extract, validate, deduplicate and save to "
        "buyers.csv without sending any email",
    )
    args, _unknown = parser.parse_known_args()

    if args.search:
        sys.exit(_cli_search())

    app.run(debug=True, port=5000)
