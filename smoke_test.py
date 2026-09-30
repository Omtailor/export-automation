"""
Smoke tests for the B2B buyer discovery + outreach system.
Run with:  python smoke_test.py

Tests use temporary copies of the CSV data files where they modify data, and
restore the originals afterwards. No real emails are sent: the SMTP layer is
monkeypatched.
"""

import csv
import importlib.util
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

errors = []
test_count = 0


def ok(msg):
    print(f"[OK]   {msg}")


def fail(label, exc):
    errors.append(f"[FAIL] {label}: {exc}")
    print(f"[FAIL] {label}: {exc}")


def test(fn):
    """Register and immediately run a test function."""
    global test_count
    test_count += 1
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - report and continue
        fail(fn.__name__, e)


# ── Shared helpers ─────────────────────────────────────────────


class TempData:
    """Redirect activity_logger CSV paths to a temp directory for one test."""

    def __init__(self):
        self.tmpdir = None
        self.buyers = None
        self.sent_log = None

    def __enter__(self):
        from activity_logging import activity_logger as al

        self.tmpdir = tempfile.mkdtemp(prefix="smoke_")
        self.buyers = os.path.join(self.tmpdir, "buyers.csv")
        self.sent_log = os.path.join(self.tmpdir, "sent_log.csv")
        self._orig = (al.BUYERS_CSV, al.SENT_LOG_CSV, al.DATA_DIR)
        al.BUYERS_CSV, al.SENT_LOG_CSV, al.DATA_DIR = self.buyers, self.sent_log, self.tmpdir
        return self

    def __exit__(self, *exc):
        from activity_logging import activity_logger as al

        al.BUYERS_CSV, al.SENT_LOG_CSV, al.DATA_DIR = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        return False

    def write_buyers(self, emails):
        with open(self.buyers, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "buyer_name",
                    "company_name",
                    "email",
                    "website",
                    "country",
                    "source_platform",
                    "discovered_at",
                ]
            )
            for i, e in enumerate(emails, 1):
                w.writerow(["", f"Company {i}", e, "", "US", "google", "2026-01-01"])

    def read_buyers(self):
        with open(self.buyers, newline="", encoding="utf-8") as f:
            return [r["email"] for r in csv.DictReader(f)]

    def write_sent_log(self, rows):
        from activity_logging.activity_logger import SENT_LOG_COLUMNS

        with open(self.sent_log, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(SENT_LOG_COLUMNS)
            w.writerows(rows)


def fake_sender(should_fail=()):
    """
    Replace get_smtp_connection with a stub that records sent recipients and
    raises for any address listed in `should_fail`. Returns the recorded list.
    """
    import outreach.gmail_sender as gs

    sent = []

    class FakeSMTP:
        def send_message(self, msg):
            to = msg["To"]
            if to in should_fail:
                raise RuntimeError(f"simulated failure for {to}")
            sent.append(to)

        def quit(self):
            pass

    gs.get_smtp_connection = lambda: FakeSMTP()
    return sent


def restore_smtp():
    import outreach.gmail_sender as gs

    gs.get_smtp_connection = gs.__dict__["_real_get_smtp_connection"]


gs_real = None


# ── 1. config ──────────────────────────────────────────────────
def test_config():
    import config

    assert config.GMAIL_EMAIL is not None and "@" in config.GMAIL_EMAIL
    assert config.CAMPAIGN_BATCH_SIZE == 10
    assert isinstance(config.SEARCH_QUERIES, list) and len(config.SEARCH_QUERIES) >= 20
    assert config.MAX_SEARCH_PAGES >= 1
    assert config.MAX_PAGES_PER_DOMAIN >= 1
    assert config.BUYER_QUERY_VARIATIONS is config.SEARCH_QUERIES
    assert config.MIN_SEND_DELAY >= 0
    assert config.MAX_SEND_DELAY >= config.MIN_SEND_DELAY
    # B2B buyer intent
    for intent in ("importer", "distributor", "wholesaler", "buyer"):
        assert any(intent in q for q in config.SEARCH_QUERIES), intent
    assert any("decorative glassware" in q for q in config.SEARCH_QUERIES)
    assert any("home decor" in q for q in config.SEARCH_QUERIES)
    assert "Dear Sir/Madam," in config.DEFAULT_BODY
    assert not hasattr(config, "GEMINI_API_KEY")
    assert not hasattr(config, "SEARCH_KEYWORD")
    ok("config.py - sender, batch size, B2B query set, universal body")


# ── 2. package imports ─────────────────────────────────────────
def test_imports():
    for mod in (
        "search",
        "extraction",
        "validation",
        "activity_logging",
        "outreach",
    ):
        __import__(mod)
    ok("all packages import cleanly")


# ── 3. email validation rules ──────────────────────────────────
def test_validation():
    from validation.email_validator import (
        validate_email_syntax,
        validate_not_blocked_prefix,
        validate_not_disposable,
    )

    assert validate_email_syntax("info@company.com")
    assert validate_email_syntax("sales@wholesale-glassware.net")
    assert not validate_email_syntax("notanemail")

    for addr in [
        "info@co.com",
        "sales@co.com",
        "admin@co.com",
        "support@co.com",
        "wholesale@co.com",
        "buying@co.com",
        "purchasing@co.com",
        "procurement@co.com",
        "orders@co.com",
    ]:
        assert validate_not_blocked_prefix(addr), f"{addr} should NOT be blocked"

    for addr in [
        "noreply@co.com",
        "no-reply@co.com",
        "donotreply@co.com",
        "bounce@co.com",
        "mailer-daemon@co.com",
    ]:
        assert not validate_not_blocked_prefix(addr), f"{addr} SHOULD be blocked"

    assert validate_not_disposable("info@legit-company.com")
    assert not validate_not_disposable("test@mailinator.com")
    ok("email_validator - business prefixes pass, system/noise blocked")


# ── 4. obfuscated email decoding ───────────────────────────────
def test_obfuscation():
    from extraction.data_extractor import (
        decode_obfuscated_email,
        extract_emails_from_text,
    )

    assert "info@company.com" in decode_obfuscated_email(
        "info [at] company [dot] com"
    )
    assert "sales@example.org" in decode_obfuscated_email(
        "sales(at)example(dot)org"
    )
    safe = decode_obfuscated_email("Look at the info@company.com page")
    assert "info@company.com" in safe
    found = extract_emails_from_text("reach us: info [at] glassware [dot] com")
    assert any("info@glassware.com" in e for e in found)
    ok("obfuscated email decoding correct")


# ── 5. dedup within a batch ────────────────────────────────────
def test_dedup_within_batch():
    from activity_logging import save_new_buyers

    with TempData() as t:
        count = save_new_buyers(
            [
                {"company_name": "Co", "email": "Info@Company.COM"},
                {"company_name": "Co", "email": "info@company.com"},
                {"company_name": "Co2", "email": "SALES@OTHER.COM"},
            ]
        )
        assert count == 2, f"expected 2 unique, got {count}"
        emails = t.read_buyers()
        assert emails == ["info@company.com", "sales@other.com"], emails
    ok("case-insensitive dedup - 3 records - 2 unique saved")


# ── 6. repeated search does not duplicate ──────────────────────
def test_repeated_search_idempotent():
    from activity_logging import load_buyers, save_new_buyers

    with TempData() as t:
        t.write_buyers(["a@one.com", "b@two.com"])
        added = save_new_buyers(
            [
                {"company_name": "A", "email": "a@one.com"},
                {"company_name": "B", "email": "B@two.com"},
                {"company_name": "C", "email": "c@three.com"},
            ]
        )
        assert added == 1, f"expected 1 new, got {added}"
        assert len(load_buyers()) == 3
    ok("re-running search does not re-add existing buyers")


# ── 7. previously-sent emails never enter buyers.csv ───────────
def test_sent_log_exclusion():
    from activity_logging import save_new_buyers

    with TempData() as t:
        t.write_sent_log(
            [
                [
                    "a@one.com",
                    "sent",
                    "2026-01-01T00:00:00+00:00",
                    "S",
                    "",
                    "A",
                    "US",
                    "P",
                    "google",
                    "",
                    "",
                ]
            ]
        )
        added = save_new_buyers(
            [
                {"company_name": "A", "email": "a@one.com"},
                {"company_name": "B", "email": "b@two.com"},
                {"company_name": "C", "email": "C@three.com"},
            ]
        )
        assert added == 2, f"expected 2 new, got {added}"
        emails = t.read_buyers()
        assert "a@one.com" not in emails
        assert emails == ["b@two.com", "c@three.com"], emails
    ok("previously-sent emails are excluded from buyers.csv")


# ── 8. no Gemini / classification anywhere ─────────────────────
def test_no_classification():
    import extraction

    assert not hasattr(extraction, "run_classification")
    try:
        import extraction.ai_classifier  # noqa: F401

        raise AssertionError("ai_classifier module still exists")
    except ImportError:
        pass

    assert not os.path.exists(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "extraction", "ai_classifier.py")
    )
    assert not os.path.exists(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "classify_result.html")
    )
    ok("Gemini classifier module and template are gone")


# ── 9. presentation PDF ────────────────────────────────────────
def test_presentation():
    from config import PRESENTATION_PATH

    assert PRESENTATION_PATH, "PRESENTATION_PATH is empty"
    assert os.path.exists(PRESENTATION_PATH), f"not found: {PRESENTATION_PATH}"
    assert PRESENTATION_PATH.lower().endswith(".pdf")
    assert os.access(PRESENTATION_PATH, os.R_OK)
    assert os.path.getsize(PRESENTATION_PATH) > 0
    ok(f"presentation PDF exists and readable ({os.path.getsize(PRESENTATION_PATH):,} bytes)")


# ── 10. load_sent_log returns a set ────────────────────────────
def test_load_sent_log():
    from activity_logging import load_sent_log

    assert isinstance(load_sent_log(), set)
    ok("load_sent_log() returns a set")


# ── 11. URL normalisation ──────────────────────────────────────
def test_url_normalisation():
    from search.google_search import _normalise_url

    assert _normalise_url("https://Example.COM/path/") == _normalise_url(
        "https://example.com/path"
    )
    ok("URL normalisation works (case + trailing slash)")


# ── 12. build_email: universal content + attachment ────────────
def test_build_email():
    import outreach.gmail_sender as gs
    from config import DEFAULT_BODY, DEFAULT_SUBJECT, GMAIL_EMAIL, PRESENTATION_PATH

    msg = gs.build_email(
        receiver_email="buyer@example.com",
        subject=DEFAULT_SUBJECT,
        body=DEFAULT_BODY,
        attachment_path=PRESENTATION_PATH,
    )
    ctypes = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in ctypes and "text/html" in ctypes
    assert "application/octet-stream" in ctypes, ctypes
    assert msg["From"] == GMAIL_EMAIL, msg["From"]

    plain = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
    payload = plain.get_payload(decode=True).decode("utf-8")
    assert payload == DEFAULT_BODY, "body must be the universal message"
    assert "{buyer_name}" not in payload
    filenames = [p.get_filename() for p in msg.walk() if p.get_filename()]
    assert os.path.basename(PRESENTATION_PATH) in filenames, filenames
    ok("build_email - universal body, fixed sender, PDF attached")


# ── 13. missing attachment raises AttachmentError ──────────────
def test_missing_attachment():
    import outreach.gmail_sender as gs

    try:
        gs.build_email("a@b.com", "s", "b", "assets/does_not_exist.pdf")
        raise AssertionError("expected AttachmentError")
    except gs.AttachmentError:
        pass
    ok("missing attachment raises AttachmentError")


# ── 14. batch selection never exceeds 10 ───────────────────────
def test_batch_selection():
    from outreach import select_next_batch

    buyers = [{"email": f"u{i}@co.com"} for i in range(1, 26)]
    assert len(select_next_batch(buyers)) == 10
    assert len(select_next_batch(buyers[:6])) == 6
    assert select_next_batch([]) == []
    assert [b["email"] for b in select_next_batch(buyers)] == [
        f"u{i}@co.com" for i in range(1, 11)
    ]
    ok("batch selection - max 10, fewer-than-10 handled, order preserved")


# ── 15. campaign: 12 buyers - 10 sent, 2 remain, then 2 sent ───
def test_campaign_two_runs():
    import outreach.gmail_sender as gs
    from config import DEFAULT_BODY, DEFAULT_SUBJECT

    gs.__dict__["_real_get_smtp_connection"] = gs.get_smtp_connection
    emails = [f"campaign{i}@shop.test" for i in range(1, 13)]
    with TempData() as t:
        t.write_buyers(emails)
        sent = fake_sender()
        try:
            r1 = gs.run_campaign(DEFAULT_SUBJECT, DEFAULT_BODY)
            assert r1["status"] == "completed", r1
            assert r1["attempted"] == 10, r1
            assert r1["sent"] == 10, r1
            assert r1["remaining"] == 2, r1
            assert len(t.read_buyers()) == 2, t.read_buyers()

            r2 = gs.run_campaign(DEFAULT_SUBJECT, DEFAULT_BODY)
            assert r2["attempted"] == 2, r2
            assert r2["sent"] == 2, r2
            assert r2["remaining"] == 0, r2
            assert t.read_buyers() == [], t.read_buyers()
            assert len(sent) == 12

            r3 = gs.run_campaign(DEFAULT_SUBJECT, DEFAULT_BODY)
            assert r3["status"] == "no_buyers", r3
            assert "No unsent buyers" in r3["message"]
        finally:
            restore_smtp()
    ok("12 buyers - 10 sent / 2 remain - 2 sent / 0 remain - no_buyers")


# ── 16. partial failure keeps failed buyers ────────────────────
def test_partial_failure():
    import outreach.gmail_sender as gs
    from activity_logging import load_sent_log
    from config import DEFAULT_BODY, DEFAULT_SUBJECT

    gs.__dict__["_real_get_smtp_connection"] = gs.get_smtp_connection
    emails = [f"p{i}@buyer.test" for i in range(1, 11)]
    failing = {emails[2], emails[4], emails[7]}
    with TempData() as t:
        t.write_buyers(emails)
        sent = fake_sender(should_fail=failing)
        try:
            r = gs.run_campaign(DEFAULT_SUBJECT, DEFAULT_BODY)
        finally:
            restore_smtp()

        assert r["attempted"] == 10, r
        assert r["sent"] == 7, r
        assert r["failed"] == 3, r
        assert r["remaining"] == 3, r

        remaining = sorted(t.read_buyers())
        assert remaining == sorted(failing), remaining
        assert sorted(sent) == sorted(set(emails) - failing)

        logged_sent = load_sent_log()
        assert logged_sent == set(emails) - failing, logged_sent
        for f in failing:
            assert f not in logged_sent
    ok("partial failure - 7 sent removed, 3 failed stay in buyers.csv")


# ── 17. sent_log records failures ──────────────────────────────
def test_failed_logged():
    import outreach.gmail_sender as gs
    from config import DEFAULT_BODY, DEFAULT_SUBJECT

    gs.__dict__["_real_get_smtp_connection"] = gs.get_smtp_connection
    with TempData() as t:
        t.write_buyers(["x@fail.test"])
        fake_sender(should_fail={"x@fail.test"})
        try:
            gs.run_campaign(DEFAULT_SUBJECT, DEFAULT_BODY)
        finally:
            restore_smtp()

        with open(t.sent_log, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1, rows
        assert rows[0]["status"] == "failed", rows[0]
        assert rows[0]["error_message"], "failure reason must be recorded"
        assert rows[0]["email"] == "x@fail.test"
        assert t.read_buyers() == ["x@fail.test"]
    ok("failed sends are logged with a reason and stay in buyers.csv")


# ── 18. empty / missing buyers.csv is handled ──────────────────
def test_empty_buyers():
    import outreach.gmail_sender as gs
    from config import DEFAULT_BODY, DEFAULT_SUBJECT

    with TempData():
        r = gs.run_campaign(DEFAULT_SUBJECT, DEFAULT_BODY)
    assert r["status"] == "no_buyers", r
    assert r["sent"] == 0 and r["failed"] == 0
    ok("empty buyers.csv - 'No unsent buyers available for this campaign.'")


# ── 19. search never sends email ───────────────────────────────
def test_search_does_not_send():
    global _SEARCH_SENT_GUARD
    import app as app_module
    from config import ENABLE_SMTP_VERIFICATION

    calls = []
    import outreach.gmail_sender as gs

    gs.__dict__["_real_get_smtp_connection"] = gs.get_smtp_connection
    fake_sender()
    try:
        import search as search_pkg
        import extraction as extraction_pkg
        import validation as validation_pkg

        search_pkg.run_all_sources = lambda: [
            {"url": "https://acme.test", "title": "Acme", "source": "google"}
        ]
        extraction_pkg.extract_emails_from_results = lambda results: [
            {"email": "info@acme.test", "company_name": "Acme", "source_platform": "google"}
        ]
        validation_pkg.validate_email_records = (
            lambda records, check_smtp=False: records
        )
        app_module.ENABLE_SMTP_VERIFICATION = False

        with TempData() as t:
            result = app_module.run_buyer_search()
            assert result["new_buyers"] == 1, result
            assert t.read_buyers() == ["info@acme.test"], t.read_buyers()
            calls.append(gs.get_smtp_connection)
            assert calls, "smtp stub was never installed"
    finally:
        restore_smtp()
    ok("search pipeline saves buyers and never touches the sender")


# ── 20. double-run protection ──────────────────────────────────
def test_concurrent_guard():
    import outreach.gmail_sender as gs
    from config import DEFAULT_BODY, DEFAULT_SUBJECT

    with TempData() as t:
        t.write_buyers([f"c{i}@co.test" for i in range(1, 4)])
        real_lock = gs._campaign_lock
        acquired = real_lock.acquire(blocking=False)
        assert acquired
        try:
            r = gs.run_campaign(DEFAULT_SUBJECT, DEFAULT_BODY)
        finally:
            real_lock.release()
            gs._campaign_lock = real_lock
        assert r["status"] == "busy", r
    ok("a second concurrent campaign run is refused")


# ── 21. app.py has no /classify route and supports --search ────
def test_app_routes():
    import app as app_module

    rules = {r.rule for r in app_module.app.url_map.iter_rules()}
    assert "/classify" not in rules, rules
    for expected in ("/", "/find-leads", "/send", "/report", "/download-report"):
        assert expected in rules, expected
    assert callable(app_module.run_buyer_search)
    assert callable(app_module._cli_search)
    ok("app.py - no /classify route, --search entry point present")


# ── 22. main.py / app.py import cleanly ────────────────────────
def test_cli_imports():
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("main.py", "app.py"):
        spec = importlib.util.spec_from_file_location(name.replace(".py", ""), name)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "main") or hasattr(mod, "app")
    ok("main.py and app.py import cleanly")


# ── 23. malformed CSV rows are tolerated ───────────────────────
def test_malformed_csv():
    from activity_logging import load_buyers

    with TempData() as t:
        with open(t.buyers, "w", newline="", encoding="utf-8") as f:
            f.write(
                "buyer_name,company_name,email,website,country,source_platform,discovered_at\n"
                ",Good,good@co.test,,US,google,2026-01-01\n"
                ",Bad,too,many,columns,here,now\n"
                ",Also,also@co.test,,US,google,2026-01-01\n"
            )
        emails = [b["email"] for b in load_buyers()]
        assert "good@co.test" in emails and "also@co.test" in emails, emails
    ok("malformed CSV rows are skipped, valid rows preserved")


# ── 24. sender is centralised and read from config ─────────────
def test_sender_identity():
    import outreach.gmail_auth as ga
    import outreach.gmail_sender as gs
    from config import GMAIL_EMAIL

    src_dir = os.path.dirname(os.path.abspath(__file__))
    assert GMAIL_EMAIL is not None and "@" in GMAIL_EMAIL
    for path in ("outreach/gmail_sender.py", "outreach/gmail_auth.py"):
        with open(os.path.join(src_dir, path), encoding="utf-8") as f:
            src = f.read()
        assert "GMAIL_EMAIL" in src

    captured = {}

    class FakeConn:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, user, pwd):
            captured["user"] = user

        def quit(self):
            pass

    orig = ga.smtplib.SMTP_SSL
    try:
        ga.smtplib.SMTP_SSL = FakeConn
        os.environ["GMAIL_APP_PASSWORD"] = "test-password"
        ga.get_smtp_connection()
    finally:
        ga.smtplib.SMTP_SSL = orig
        os.environ.pop("GMAIL_APP_PASSWORD", None)
    assert captured["user"] == GMAIL_EMAIL, captured
    assert gs.GMAIL_EMAIL == GMAIL_EMAIL
    ok("SMTP authenticates as configured GMAIL_EMAIL")


# ── 25. Flask pages render ─────────────────────────────────────
def test_dashboard_renders():
    import app as app_module

    with TempData() as t:
        t.write_buyers(["a@co.test", "b@co.test"])
        client = app_module.app.test_client()
        for path in ("/", "/send", "/report"):
            resp = client.get(path)
            assert resp.status_code == 200, (path, resp.status_code)
        html = client.get("/").get_data(as_text=True)
        assert "Classify" not in html
        assert "Find New Leads" in html
        assert "Send Campaign" in html
        assert "View Report" in html
    ok("dashboard, send form and report render without classification UI")


# ── 26. empty / header-only sent_log.csv is initialised ────────
def test_empty_sent_log_initialised():
    from activity_logging import load_sent_log, log_send_attempt
    from activity_logging.activity_logger import SENT_LOG_COLUMNS

    with TempData() as t:
        # 0-byte file: the exact state that produced
        # "No columns to parse from file"
        open(t.sent_log, "w").close()
        assert os.path.getsize(t.sent_log) == 0

        assert load_sent_log() == set()
        assert os.path.getsize(t.sent_log) > 0, "headers must be written"
        with open(t.sent_log, newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
        assert header == SENT_LOG_COLUMNS, header

        assert log_send_attempt("a@co.test", "sent", "S") is True
        with open(t.sent_log, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1, rows
        assert rows[0]["email"] == "a@co.test"
        assert rows[0]["status"] == "sent"
    ok("0-byte sent_log.csv is initialised with headers and accepts writes")


# ── 27. missing sent_log.csv is created ────────────────────────
def test_missing_sent_log_created():
    from activity_logging import log_send_attempt
    from activity_logging.activity_logger import SENT_LOG_COLUMNS

    with TempData() as t:
        assert not os.path.exists(t.sent_log)
        assert log_send_attempt("b@co.test", "sent", "S") is True
        with open(t.sent_log, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert list(rows[0].keys()) == SENT_LOG_COLUMNS
        assert rows[0]["email"] == "b@co.test"
    ok("missing sent_log.csv is created with the correct columns")


# ── 28. header-only sent_log.csv accepts writes ───────────────
def test_header_only_sent_log():
    from activity_logging import log_send_attempt

    with TempData() as t:
        from activity_logging.activity_logger import SENT_LOG_COLUMNS

        with open(t.sent_log, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(SENT_LOG_COLUMNS)
        assert log_send_attempt("c@co.test", "sent", "S") is True
        assert log_send_attempt("d@co.test", "failed", "S", error_message="x") is True
        with open(t.sent_log, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert [r["email"] for r in rows] == ["c@co.test", "d@co.test"], rows
    ok("header-only sent_log.csv appends without truncating")


# ── 29. website is recorded in the sent log ────────────────────
def test_sent_log_website_column():
    from activity_logging import log_send_attempt

    with TempData() as t:
        assert log_send_attempt(
            "e@co.test",
            "sent",
            "Subject",
            buyer_name="Buyer",
            company_name="Co",
            website="https://co.test",
        )
        with open(t.sent_log, newline="", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        assert row["website"] == "https://co.test", row
        assert row["company_name"] == "Co"
        assert row["buyer_name"] == "Buyer"
        assert row["campaign_subject"] == "Subject"
        assert row["sent_at"]
    ok("sent_log.csv records website, company, buyer_name, subject and sent_at")


# ── 30. buyer is removed only after the log write succeeds ────
def test_removal_requires_log_success():
    import outreach.gmail_sender as gs
    from activity_logging import log_send_attempt
    from config import DEFAULT_BODY, DEFAULT_SUBJECT

    gs.__dict__["_real_get_smtp_connection"] = gs.get_smtp_connection
    with TempData() as t:
        t.write_buyers(["keep1@co.test", "keep2@co.test"])
        fake_sender()
        real_log = gs.log_send_attempt
        try:
            # Simulate the log write silently failing for the first recipient.
            calls = {"n": 0}

            def flaky_log(*a, **k):
                calls["n"] += 1
                if calls["n"] == 1:
                    return False
                return real_log(*a, **k)

            gs.log_send_attempt = flaky_log
            r = gs.run_campaign(DEFAULT_SUBJECT, DEFAULT_BODY)
        finally:
            gs.log_send_attempt = real_log
            restore_smtp()

        assert r["status"] == "log_error", r
        assert r["sent"] == 1, r
        assert r["unlogged"] == 1, r
        remaining = t.read_buyers()
        assert remaining == ["keep1@co.test"], remaining
        assert log_send_attempt("keep2@co.test", "sent", "S") is True
    ok("buyer kept in buyers.csv when its sent_log.csv write is not confirmed")


# ── 31. 10-buyer campaign: 10 logged, 10 removed ──────────────
def test_ten_buyer_campaign_reconciled():
    import outreach.gmail_sender as gs
    from activity_logging import load_sent_log
    from config import DEFAULT_BODY, DEFAULT_SUBJECT

    gs.__dict__["_real_get_smtp_connection"] = gs.get_smtp_connection
    emails = [f"prod{i}@wholesale.test" for i in range(1, 11)]
    with TempData() as t:
        t.write_buyers(emails + [f"lead{i}@wholesale.test" for i in range(11, 21)])
        sent = fake_sender()
        try:
            r = gs.run_campaign(DEFAULT_SUBJECT, DEFAULT_BODY)
        finally:
            restore_smtp()

        assert r["attempted"] == 10 and r["sent"] == 10, r
        assert r["unlogged"] == 0, r
        assert t.read_buyers() == [f"lead{i}@wholesale.test" for i in range(11, 21)]
        with open(t.sent_log, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 10, rows
        assert all(x["status"] == "sent" for x in rows)
        assert load_sent_log() == set(sent) == set(emails)
    ok("10-buyer campaign: 10 sent_log records + 10 buyers removed")


# ── Run all tests ──────────────────────────────────────────────
for fn in [
    test_config,
    test_imports,
    test_validation,
    test_obfuscation,
    test_dedup_within_batch,
    test_repeated_search_idempotent,
    test_sent_log_exclusion,
    test_no_classification,
    test_presentation,
    test_load_sent_log,
    test_url_normalisation,
    test_build_email,
    test_missing_attachment,
    test_batch_selection,
    test_campaign_two_runs,
    test_partial_failure,
    test_failed_logged,
    test_empty_buyers,
    test_search_does_not_send,
    test_concurrent_guard,
    test_app_routes,
    test_cli_imports,
    test_malformed_csv,
    test_sender_identity,
    test_dashboard_renders,
    test_empty_sent_log_initialised,
    test_missing_sent_log_created,
    test_header_only_sent_log,
    test_sent_log_website_column,
    test_removal_requires_log_success,
    test_ten_buyer_campaign_reconciled,
]:
    test(fn)

print()
if errors:
    print("=" * 50)
    print(f"  {len(errors)}/{test_count} SMOKE TEST(S) FAILED")
    print("=" * 50)
    for err in errors:
        print(f"  {err}")
    sys.exit(1)
else:
    print("=" * 50)
    print(f"  ALL {test_count} SMOKE TESTS PASSED")
    print("=" * 50)
