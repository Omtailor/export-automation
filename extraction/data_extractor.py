"""
Website crawling and email extraction module.

Responsibilities:
- Fetch pages with requests (Selenium only as last-resort fallback)
- Crawl common contact/about/wholesale paths per domain
- Decode obfuscated email addresses safely
- Extract mailto: links
- Tag WHOIS-derived emails separately (lower confidence)
- Deduplicate URLs within a single run
"""

import requests
from bs4 import BeautifulSoup
import re
import logging
import os
import sys
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import MAX_PAGES_PER_DOMAIN, PRODUCT_TERM, PRODUCT_TERMS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EMAIL_REGEX = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"

# Contact-page paths to probe, ordered by likely payoff.
# Spec explicitly requires: /about-us /wholesale-inquiry /distributors /purchasing /procurement
CONTACT_PATHS = [
    "/contact",
    "/contact-us",
    "/contact.html",
    "/about",
    "/about-us",
    "/team",
    "/wholesale",
    "/wholesale-inquiry",
    "/import",
    "/distributor",
    "/distributors",
    "/purchasing",
    "/procurement",
]

# Domains whose result pages are platforms or social/search sites. Generic
# company paths are useful on a company domain, but noisy on these roots.
PLATFORM_DOMAINS = {
    "reddit.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "yelp.com",
    "amazon.com",
    "etsy.com",
    "faire.com",
    "globalsources.com",
    "tradekey.com",
    "go4worldbusiness.com",
    "exporthub.com",
    "alibaba.com",
    "indiamart.com",
    "humanitix.com",
    "spotify.com",
    "justdial.com",
    "squarespace.com",
    "x.com",
}

_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "srsltid",
}

_INFRASTRUCTURE_LOCAL_PARTS = {
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
    "notifications",
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
_DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
_BUYER_SIGNALS = (
    "buyer",
    "buying",
    "import",
    "distribut",
    "wholesale",
    "retail",
    "supplier",
    "decor",
    "glassware",
    "home accent",
)
_DOCUMENT_NOISE_SIGNALS = (
    "conference",
    "research paper",
    "market research",
    "journal",
    "university",
    "proceedings",
    "thesis",
)
_RELEVANCE_TERMS = {
    PRODUCT_TERM.lower(),
    *(term.lower() for term in PRODUCT_TERMS),
    "glassware",
    "home decor",
    "home décor",
    "vase",
    "candle",
    "votive",
    "jar",
    "wholesale",
    "importer",
    "distributor",
    "retailer",
    "buyer",
}
_URL_TIMEOUT_SECONDS = 15
_BLOCKED_EXTRACTION_DOMAINS = {
    "cloudflare.com",
    "globalcbpr.org",
    "facebook.com",
}

# Single shared User-Agent string
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA}

# Per-run URL fetch caches and deduplication state.
_page_cache: dict[str, str] = {}
_html_cache: dict[str, str] = {}
_processed_urls: set[str] = set()
_fetched_urls: set[str] = set()
_whois_cache: dict[str, list[str]] = {}

# Selenium is deliberately lazy and disabled for the rest of a run after one
# initialization failure.  This prevents a broken driver from being retried
# for every URL.
_selenium_driver = None
_selenium_disabled = False
_selenium_failure_logged = False
_selenium_attempted_domains: set[str] = set()

# Per-run domain crawl tracker: prevents crawling same domain more than MAX_PAGES_PER_DOMAIN times
_domain_page_counts: dict[str, int] = {}
_cache_lock = threading.RLock()
_selenium_lock = threading.Lock()


def _get_domain(url: str) -> str:
    """Extract netloc from a URL, stripping www."""
    try:
        hostname = urlparse(url).hostname or ""
        return hostname.removeprefix("www.").lower()
    except Exception:
        return ""


def _reset_run_caches():
    """Call this at the start of each extraction run to reset per-run state."""
    global _selenium_driver, _selenium_disabled, _selenium_failure_logged
    if _selenium_driver is not None:
        try:
            _selenium_driver.quit()
        except Exception:
            pass
    _selenium_driver = None
    _selenium_disabled = False
    _selenium_failure_logged = False
    _selenium_attempted_domains.clear()
    _page_cache.clear()
    _html_cache.clear()
    _processed_urls.clear()
    _fetched_urls.clear()
    _whois_cache.clear()
    _domain_page_counts.clear()


def _normalize_url(url: str) -> str:
    """Create a stable per-run URL key while preserving useful query values."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_KEYS
        and not key.lower().startswith("utm_")
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            urlencode(sorted(query)),
            "",
        )
    )


def _is_platform_domain(domain: str) -> bool:
    return any(
        domain == platform or domain.endswith(f".{platform}")
        for platform in PLATFORM_DOMAINS
    )


def _is_blocked_extraction_domain(domain: str) -> bool:
    return any(
        domain == blocked or domain.endswith(f".{blocked}")
        for blocked in _BLOCKED_EXTRACTION_DOMAINS
    )


def _is_relevant_page(url: str, text: str, context: str = "") -> bool:
    haystack = f"{url} {context} {text}".lower()
    return any(term in haystack for term in _RELEVANCE_TERMS)


def _is_infrastructure_email(email: str) -> bool:
    local = (
        email.split("@", 1)[0].strip().lower()
        if "@" in email
        else email.strip().lower()
    )
    return local in _INFRASTRUCTURE_LOCAL_PARTS


def _is_placeholder_email(email: str) -> bool:
    local, _, domain = email.strip().lower().partition("@")
    return domain in _PLACEHOLDER_DOMAINS or local in _PLACEHOLDER_LOCAL_PARTS


def _is_document_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(_DOCUMENT_EXTENSIONS)


def _is_irrelevant_document(url: str, context: str) -> bool:
    if not _is_document_url(url):
        return False
    lowered = context.lower()
    has_buyer_signal = any(signal in lowered for signal in _BUYER_SIGNALS)
    has_noise_signal = any(signal in lowered for signal in _DOCUMENT_NOISE_SIGNALS)
    return has_noise_signal and not has_buyer_signal


def _can_crawl_domain(domain: str) -> bool:
    """Return True if we haven't exceeded MAX_PAGES_PER_DOMAIN for this domain."""
    return _domain_page_counts.get(domain, 0) < MAX_PAGES_PER_DOMAIN


def _record_domain_fetch(domain: str):
    _domain_page_counts[domain] = _domain_page_counts.get(domain, 0) + 1


# ──────────────────────────────────────────────────────────────
# Obfuscated email decoding
# ──────────────────────────────────────────────────────────────

# Pattern: must be preceded by a word character (part of an email local-part)
# and the replacement token must be surrounded by non-alpha context or spaces.
# We decode only when the token is isolated (spaces or brackets around it),
# not when 'at' appears as part of an English word mid-sentence.
_OBFUSCATION_AT = re.compile(
    r"(?<=[a-zA-Z0-9._+\-])"  # immediately after valid email char
    r"\s*(?:\[at\]|\(at\)|(?<!\w)AT(?!\w))\s*"  # [at] / (at) / AT (whole word)
    r"(?=[a-zA-Z0-9])",  # immediately before domain start
    re.IGNORECASE,
)
_OBFUSCATION_DOT = re.compile(
    r"(?<=[a-zA-Z0-9])"  # after domain char
    r"\s*(?:\[dot\]|\(dot\)|(?<!\w)DOT(?!\w))\s*"
    r"(?=[a-zA-Z0-9])",
    re.IGNORECASE,
)


def decode_obfuscated_email(text: str) -> str:
    """
    Decode obfuscated email tokens safely.

    Handles:
      info [at] company [dot] com
      info(at)company(dot)com
      info AT company DOT com   (only when AT/DOT are isolated tokens)

    Does NOT corrupt normal English sentences like "Look at the contact page."
    """
    text = _OBFUSCATION_AT.sub("@", text)
    text = _OBFUSCATION_DOT.sub(".", text)
    return text


# ──────────────────────────────────────────────────────────────
# Page fetching
# ──────────────────────────────────────────────────────────────


def extract_page_text(url: str, use_selenium: bool = False) -> str:
    """
    Fetch page text.  Returns empty string on any failure.

    Order of preference:
      1. In-memory cache (avoids duplicate fetches in the same run)
      2. requests + BeautifulSoup
      3. Selenium (only when use_selenium=True AND requests returned too little content)

    Selenium is never used as the default — only as an explicit fallback.
    """
    text, _, _, _ = _fetch_page(url, allow_selenium=use_selenium)
    return text


def _fetch_with_requests(url: str, timeout: float = 8) -> tuple[str, str, bool]:
    """Fetch and parse a page with requests. Returns text, HTML, and failure state."""
    try:
        response = requests.get(url, headers=_HEADERS, timeout=max(0.5, timeout))
        response.raise_for_status()
        return _parse_html(response.text)
    except requests.HTTPError as e:
        # A normal 404/403 response is still a completed requests fetch. Do not
        # launch a browser just because a conventional contact path is absent.
        response = getattr(e, "response", None)
        if response is not None and getattr(response, "text", ""):
            return _parse_html(response.text)
        logger.debug(f"[requests] HTTP error for {url}: {e}")
        return "", "", False
    except requests.RequestException as e:
        logger.debug(f"[requests] failed for {url}: {e}")
        return "", "", True


def _parse_html(html: str) -> tuple[str, str, bool]:
    """Return visible text, original HTML, and a false failure flag."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return soup.get_text(separator=" ", strip=True), html, False


def _extract_relevant_links(url: str, html: str) -> list[str]:
    """Find useful links without inventing platform-level company paths."""
    if not html:
        return []
    domain = _get_domain(url)
    platform_page = _is_platform_domain(domain)
    keywords = (
        "contact",
        "about",
        "team",
        "wholesale",
        "import",
        "distributor",
        "purchas",
        "procurement",
    )
    links = []
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("a", href=True):
        candidate = _normalize_url(urljoin(url, tag["href"].split("#", 1)[0]))
        candidate_domain = _get_domain(candidate)
        if not candidate or not candidate.startswith(("http://", "https://")):
            continue
        path = urlparse(candidate).path.lower().rstrip("/")
        anchor_text = tag.get_text(" ", strip=True).lower()
        relevant_link = any(
            keyword in path or keyword in anchor_text for keyword in keywords
        )
        if platform_page:
            # Platform pages may expose an explicit company/contact link, but
            # their own /contact or /about paths are not buyer pages.
            external_company_link = (
                candidate_domain != domain
                and not _is_platform_domain(candidate_domain)
                and bool(anchor_text)
            )
            relevant_link = candidate_domain != domain and (
                relevant_link or external_company_link
            )
        elif candidate_domain != domain:
            relevant_link = False
        if relevant_link and candidate not in links:
            links.append(candidate)
    return links


def _looks_js_rendered(text: str, html: str) -> bool:
    """Conservative indicators for a client-rendered page shell."""
    if len(text.strip()) >= 200:
        return False
    lowered = html.lower()
    markers = (
        'id="root"',
        "id='root'",
        'id="app"',
        "id='app'",
        "__next_data__",
        "webpack",
        "enable javascript",
        "javascript required",
    )
    return any(marker in lowered for marker in markers)


def _needs_selenium(
    text: str,
    html: str,
    request_failed: bool,
    emails: list[str] | None = None,
    links: list[str] | None = None,
) -> bool:
    """Use Selenium only when requests failed or produced no useful evidence."""
    if request_failed:
        return True
    if emails or links:
        return False
    return _looks_js_rendered(text, html)


def _resolve_chromedriver_path(path: str) -> str:
    """Resolve webdriver-manager's Windows notice path to a real executable."""
    candidates = [path]
    directory = os.path.dirname(path)
    if directory and os.path.isdir(directory):
        candidates.extend(
            os.path.join(directory, name)
            for name in os.listdir(directory)
            if name.lower() == "chromedriver.exe"
        )
    for candidate in candidates:
        try:
            with open(candidate, "rb") as executable:
                if candidate.lower().endswith(".exe") and executable.read(2) == b"MZ":
                    return candidate
        except (OSError, TypeError):
            continue
    return ""


def _get_selenium_driver():
    """Create one lazy driver, or disable Selenium for this run on failure."""
    global _selenium_driver, _selenium_disabled, _selenium_failure_logged
    if _selenium_driver is not None or _selenium_disabled:
        return _selenium_driver
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from webdriver_manager.chrome import ChromeDriverManager

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-webrtc")
        options.add_argument(
            "--disable-features=WebRtcHideLocalIpsWithMdns,WebRTC,WebRtcAllowLegacyTLSProtocols"
        )
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-component-update")
        options.add_argument(
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"
        )
        options.add_argument(f"user-agent={_UA}")

        driver_path = _resolve_chromedriver_path(ChromeDriverManager().install())
        if driver_path:
            _selenium_driver = webdriver.Chrome(
                service=Service(driver_path), options=options
            )
        else:
            # Selenium Manager is the compatibility fallback when webdriver-manager
            # returns a non-executable artifact on Windows.
            _selenium_driver = webdriver.Chrome(options=options)
        logger.info("[Selenium] browser initialized; reusing it for this run")
        return _selenium_driver
    except Exception as e:
        _selenium_disabled = True
        if not _selenium_failure_logged:
            logger.warning(
                f"[Selenium] unavailable for this run, falling back to requests: {e}"
            )
            _selenium_failure_logged = True
        return None


def _fetch_with_selenium(url: str, timeout: float = 8) -> tuple[str, str]:
    """
    Selenium fallback for JavaScript-heavy pages.
    Returns empty string (does NOT raise) on any failure.
    """
    try:
        with _selenium_lock:
            driver = _get_selenium_driver()
            if driver is None:
                return "", ""
            driver.set_page_load_timeout(max(1, int(timeout)))
            driver.get(url)
        time.sleep(1)
        source = driver.page_source
        text, html, _ = _parse_html(source)
        logger.info(f"[Selenium] success for {url}")
        return text, html

    except Exception as e:
        global _selenium_driver, _selenium_disabled
        if _selenium_driver is not None:
            try:
                _selenium_driver.quit()
            except Exception:
                pass
            _selenium_driver = None
        _selenium_disabled = True
        logger.warning(
            f"[Selenium] failed for {url}: {e} — continuing with requests result"
        )
        return "", ""


# ──────────────────────────────────────────────────────────────
# Email extraction helpers
# ──────────────────────────────────────────────────────────────


def extract_emails_from_text(text: str) -> list[str]:
    """
    Extract email addresses from arbitrary text.
    Decodes obfuscated forms first, then applies regex.
    Also extracts mailto: links embedded in the raw text.
    """
    decoded = decode_obfuscated_email(text)

    # Standard regex extraction
    emails = set(re.findall(EMAIL_REGEX, decoded))

    # Also capture any mailto: references that regex might miss (e.g. "mailto:info@co.com")
    mailto_matches = re.findall(
        r"mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
        text,
        re.IGNORECASE,
    )
    emails.update(mailto_matches)

    # Remove anything that ends with a file extension (image artefacts)
    image_exts = {".png", ".jpg", ".gif", ".svg", ".jpeg", ".webp"}
    emails = {
        e for e in emails if not any(e.lower().endswith(ext) for ext in image_exts)
    }

    return list(emails)


def extract_emails_from_html(html: str) -> list[str]:
    """
    Extract emails directly from raw HTML (before stripping tags).
    Catches mailto: href values that might not appear in plain text.
    """
    emails = set()
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if href.lower().startswith("mailto:"):
                addr = href[7:].split("?")[0].strip()
                if re.match(EMAIL_REGEX, addr):
                    emails.add(addr.lower())
    except Exception:
        pass
    return list(emails)


def _fetch_page(
    url: str,
    allow_selenium: bool = True,
    relevance_context: str = "",
    deadline: float | None = None,
) -> tuple[str, str, list[str], list[str]]:
    """Fetch one URL once, returning text, HTML, emails, and useful links."""
    normalized_url = _normalize_url(url)
    if not normalized_url:
        return "", "", [], []
    if normalized_url in _processed_urls:
        logger.info(f"[Extraction] duplicate URL skipped: {normalized_url}")
        text = _page_cache.get(normalized_url, "")
        html = _html_cache.get(normalized_url, "")
        emails = []
        if _is_relevant_page(normalized_url, text, relevance_context):
            emails = extract_emails_from_text(text) + extract_emails_from_html(html)
        return (
            text,
            html,
            list(dict.fromkeys(emails)),
            _extract_relevant_links(normalized_url, html),
        )
    _processed_urls.add(normalized_url)
    if normalized_url in _page_cache:
        text = _page_cache[normalized_url]
        html = _html_cache.get(normalized_url, "")
        emails = extract_emails_from_text(text) + extract_emails_from_html(html)
        return (
            text,
            html,
            list(dict.fromkeys(emails)),
            _extract_relevant_links(normalized_url, html),
        )

    domain = _get_domain(normalized_url)
    if _is_blocked_extraction_domain(domain):
        logger.info(f"[Extraction] skipped - blocked domain: {domain}")
        return "", "", [], []
    if not _can_crawl_domain(domain):
        logger.debug(
            f"[Crawler] domain page limit reached for {domain}, skipping {url}"
        )
        return "", "", [], []

    if deadline is not None and time.monotonic() >= deadline:
        return "", "", [], []
    remaining = deadline - time.monotonic() if deadline is not None else 8
    text, html, request_failed = _fetch_with_requests(normalized_url, min(8, remaining))
    _record_domain_fetch(domain)
    _fetched_urls.add(normalized_url)
    page_relevant = _is_relevant_page(normalized_url, text, relevance_context)
    if not page_relevant:
        logger.info(f"[Extraction] skipped - not relevant: {normalized_url}")
        emails = []
    else:
        emails = extract_emails_from_text(text) + extract_emails_from_html(html)
    emails = list(dict.fromkeys(emails))
    links = _extract_relevant_links(normalized_url, html)
    request_status = "failed" if request_failed else "success"
    logger.info(
        f"[Extraction] requests -> {request_status} -> {len(emails)} email(s): {normalized_url}"
    )

    if (
        page_relevant
        and allow_selenium
        and domain not in _selenium_attempted_domains
        and _needs_selenium(text, html, request_failed, emails, links)
    ):
        _selenium_attempted_domains.add(domain)
        logger.info(f"[Selenium] fallback required for {domain}")
        remaining = deadline - time.monotonic() if deadline is not None else 8
        if remaining <= 0:
            return text, html, emails, links
        selenium_text, selenium_html = _fetch_with_selenium(
            normalized_url, min(8, remaining)
        )
        if selenium_text:
            text, html = selenium_text, selenium_html
            emails = list(
                dict.fromkeys(
                    extract_emails_from_text(text) + extract_emails_from_html(html)
                )
            )
            links = _extract_relevant_links(normalized_url, html)

    _page_cache[normalized_url] = text
    _html_cache[normalized_url] = html
    return text, html, emails, links


# ──────────────────────────────────────────────────────────────
# WHOIS lookup (tagged separately, NOT mixed with scraped emails)
# ──────────────────────────────────────────────────────────────


def get_whois_email(domain: str, timeout: float = 5) -> list[str]:
    """
    Return a list of emails from WHOIS, tagged with 'whois_source' so callers
    can treat them as lower-confidence enrichment rather than primary contacts.

    Returns plain email strings (tagging is done by the caller).
    """
    try:
        import whois  # python-whois
    except ImportError:
        logger.debug("[WHOIS] python-whois not installed, skipping.")
        return []

    # Strip protocol / path
    domain = re.sub(r"^https?://", "", domain).split("/")[0].replace("www.", "").lower()
    if domain in _whois_cache:
        logger.info(f"[WHOIS] cache hit: {domain}")
        return _whois_cache[domain]

    try:
        logger.info(f"[WHOIS] looking up {domain}")
        w = whois.whois(domain, timeout=max(1, timeout))

        raw_emails: list[str] = []
        for field in [
            w.emails,
            getattr(w, "admin_email", None),
            getattr(w, "tech_email", None),
        ]:
            if not field:
                continue
            if isinstance(field, list):
                raw_emails.extend(field)
            else:
                raw_emails.append(str(field))

        # Filter privacy/registrar addresses
        noise_keywords = [
            "privacy",
            "protection",
            "proxy",
            "whoisguard",
            "redacted",
            "abuse",
            "nic.",
        ]
        cleaned = [
            e.strip().lower()
            for e in raw_emails
            if e
            and re.match(EMAIL_REGEX, e.strip())
            and not _is_infrastructure_email(e.strip())
            and not _is_placeholder_email(e.strip())
            and not any(kw in e.lower() for kw in noise_keywords)
        ]
        unique = list(dict.fromkeys(cleaned))  # preserve order, deduplicate

        logger.info(f"[WHOIS] {domain} → {len(unique)} usable email(s)")
        _whois_cache[domain] = unique
        return unique

    except Exception as e:
        logger.debug(f"[WHOIS] lookup failed for {domain}: {e}")
        _whois_cache[domain] = []
        return []


# ──────────────────────────────────────────────────────────────
# Social links helper
# ──────────────────────────────────────────────────────────────


def extract_social_links(url: str) -> list[str]:
    """Extract LinkedIn / Facebook / Instagram profile links from a page."""
    links: list[str] = []
    try:
        response = requests.get(url, headers=_HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(
                s in href.lower()
                for s in ["linkedin.com", "facebook.com", "instagram.com"]
            ):
                links.append(href)
    except Exception as e:
        logger.debug(f"[Social] could not extract social links from {url}: {e}")
    return links


# ──────────────────────────────────────────────────────────────
# Multi-page domain crawl
# ──────────────────────────────────────────────────────────────


def extract_emails_from_multiple_pages(
    base_url: str, relevance_context: str = "", timeout_seconds: float = 15
) -> tuple[list[str], list[str]]:
    """
    Crawl the base URL plus common contact/about/wholesale sub-paths.

    Returns:
        scraped_emails : list of emails found by page scraping (main source)
        whois_emails   : list of emails from WHOIS (enrichment, lower confidence)

    Both lists contain plain lowercase email strings.
    WHOIS emails are returned separately so the caller can tag them.
    """
    scraped: list[str] = []
    seen_page_emails: set[str] = set()

    normalized_base = _normalize_url(base_url)
    deadline = time.monotonic() + timeout_seconds
    parsed_base = urlparse(normalized_base or base_url)
    base_domain_url = f"{parsed_base.scheme}://{parsed_base.netloc}"
    base_domain = _get_domain(normalized_base or base_url)

    if _is_blocked_extraction_domain(base_domain):
        logger.info(f"[Extraction] skipped - blocked domain: {base_domain}")
        return [], []

    def _add_emails(emails: list[str]):
        for e in emails:
            norm = e.lower().strip()
            if norm and norm not in seen_page_emails:
                seen_page_emails.add(norm)
                scraped.append(norm)

    # ── 1. Requests-first crawl of main and useful same-domain pages ──
    logger.info(f"[Crawler] main page: {normalized_base or base_url}")
    main_text, _, main_emails, discovered_links = _fetch_page(
        normalized_base or base_url,
        relevance_context=relevance_context,
        deadline=deadline,
    )
    _add_emails(main_emails)

    candidate_urls = list(discovered_links)
    if (
        not main_emails
        and not _is_platform_domain(base_domain)
        and not _is_document_url(normalized_base or base_url)
    ):
        candidate_urls = [
            urljoin(base_domain_url, path) for path in CONTACT_PATHS
        ] + candidate_urls
    seen_candidates = {_normalize_url(normalized_base or base_url)}

    for candidate_url in candidate_urls:
        if time.monotonic() >= deadline:
            logger.info(
                f"[Extraction] URL deadline reached: {normalized_base or base_url}"
            )
            break
        normalized_candidate = _normalize_url(candidate_url)
        if not normalized_candidate:
            continue
        if normalized_candidate in seen_candidates:
            logger.info(f"[Extraction] duplicate URL skipped: {normalized_candidate}")
            continue
        seen_candidates.add(normalized_candidate)

        if not _can_crawl_domain(_get_domain(normalized_candidate)):
            logger.debug(
                f"[Crawler] page limit hit for {_get_domain(normalized_candidate)}, stopping sub-path crawl"
            )
            break

        try:
            logger.debug(f"[Crawler] checking {normalized_candidate}")
            page_text, _, page_emails, page_links = _fetch_page(
                normalized_candidate,
                relevance_context=relevance_context,
                deadline=deadline,
            )
            if page_emails:
                logger.info(
                    f"[Crawler] {len(page_emails)} email(s) on {normalized_candidate}"
                )
            _add_emails(page_emails)
            if page_emails:
                logger.info(
                    f"[Crawler] useful email found; stopping contact-path guesses for {_get_domain(normalized_candidate)}"
                )
                break
            if page_links:
                candidate_urls.extend(page_links)
            time.sleep(0.4)
        except Exception as e:
            logger.debug(f"[Crawler] could not check {candidate_url}: {e}")
            continue

    # ── 3. WHOIS (enrichment only) ────────────────────────────
    raw_domain = base_domain
    remaining = max(1, deadline - time.monotonic())
    whois_emails = (
        [] if scraped else get_whois_email(raw_domain, timeout=min(5, remaining))
    )

    return scraped, whois_emails


# ──────────────────────────────────────────────────────────────
# Metadata helpers
# ──────────────────────────────────────────────────────────────


def extract_company_name(text: str, url: str) -> str:
    """Derive a best-guess company name from the URL domain."""
    try:
        domain = urlparse(url).netloc.replace("www.", "")
        name = domain.split(".")[0]
        return name.replace("-", " ").replace("_", " ").title()
    except Exception:
        return ""


def extract_country_from_url(url: str) -> str:
    """Infer country from ccTLD."""
    COUNTRY_MAP = {
        "US": "United States",
        "UK": "United Kingdom",
        "GB": "United Kingdom",
        "IN": "India",
        "DE": "Germany",
        "FR": "France",
        "CA": "Canada",
        "AU": "Australia",
        "JP": "Japan",
        "CN": "China",
        "BR": "Brazil",
        "NL": "Netherlands",
        "IT": "Italy",
        "ES": "Spain",
        "MX": "Mexico",
        "SG": "Singapore",
        "NZ": "New Zealand",
        "ZA": "South Africa",
        "SE": "Sweden",
        "NO": "Norway",
        "DK": "Denmark",
        "FI": "Finland",
        "AT": "Austria",
        "CH": "Switzerland",
        "BE": "Belgium",
        "PL": "Poland",
        "PT": "Portugal",
        "AE": "United Arab Emirates",
    }
    try:
        tld = urlparse(url).netloc.split(".")[-1].upper()
        return COUNTRY_MAP.get(tld, "")
    except Exception:
        return ""


def extract_buyer_name(text: str) -> str:
    """Try to extract a person name near 'contact'/'about' section headings."""
    try:
        match = re.search(r"(?:contact|about|team|owner|founder)", text, re.IGNORECASE)
        if match:
            snippet = text[max(0, match.start() - 50) : match.end() + 80]
            name_match = re.search(r"([A-Z][a-z]{2,} [A-Z][a-z]{2,})", snippet)
            if name_match:
                return name_match.group(1)
    except Exception:
        pass
    return ""


# ──────────────────────────────────────────────────────────────
# Main extraction pipeline
# ──────────────────────────────────────────────────────────────


def extract_emails_from_results(raw_results: list[dict]) -> list[dict]:
    """
    For each search result, crawl the website and extract emails.

    Returns a list of buyer record dicts (one per email).
    WHOIS-derived emails are included but tagged with source_platform='whois'.
    """
    _reset_run_caches()

    buyer_records: list[dict] = []
    seen_emails: set[str] = set()  # global dedup across all results in this run

    # Deduplicate input URLs before crawling using the same canonical key as
    # the page cache, so tracking parameters cannot create extra crawls.
    seen_input_urls: set[str] = set()
    deduped_results = []
    for r in raw_results:
        url = r.get("url", "")
        normalized_url = _normalize_url(url)
        if not normalized_url:
            continue
        if normalized_url not in seen_input_urls:
            seen_input_urls.add(normalized_url)
            r = dict(r)
            r["url"] = normalized_url
            deduped_results.append(r)
        else:
            logger.info(f"[Extraction] duplicate URL skipped: {normalized_url}")

    logger.info(
        f"[Extraction] {len(deduped_results)} unique URLs to process "
        f"(from {len(raw_results)} raw results)"
    )

    def _process_result(result: dict):
        url = result.get("url", "")
        title = result.get("title", "")
        source = result.get("source", "google")
        snippet = result.get("snippet", "")

        logger.info(f"[Extraction] processing: {url}")

        try:
            scraped_emails, whois_emails = extract_emails_from_multiple_pages(
                url, relevance_context=f"{title} {snippet}"
            )
        except Exception as e:
            logger.error(f"[Extraction] unexpected error for {url}: {e}")
            scraped_emails, whois_emails = [], []

        page_text = _page_cache.get(_normalize_url(url), title + " " + snippet)
        document_context = " ".join([url, title, snippet, page_text])
        if _is_irrelevant_document(url, document_context):
            logger.info(f"[Extraction] irrelevant document skipped: {url}")
            return []

        if not scraped_emails and not whois_emails:
            logger.info(f"[Extraction] no emails found at {url}")
            return []

        # Use the cached main-page text for metadata (no extra fetch)
        company_name = extract_company_name(page_text, url)
        country = extract_country_from_url(url)
        buyer_name = extract_buyer_name(page_text)

        records = []

        def _add_record(email: str, src_platform: str):
            norm_email = email.strip().lower()
            if not norm_email or norm_email in seen_emails:
                return
            if _is_infrastructure_email(norm_email):
                logger.info(f"[Extraction] infrastructure email skipped: {norm_email}")
                return
            if _is_placeholder_email(norm_email):
                logger.info(f"[Extraction] placeholder email skipped: {norm_email}")
                return
            records.append(
                {
                    "buyer_name": buyer_name,
                    "company_name": company_name,
                    "email": norm_email,
                    "website": url,
                    "country": country,
                    "source_platform": src_platform,
                }
            )
            logger.info(
                f"[Extraction] found email: {norm_email} ({src_platform}) @ {url}"
            )

        for email in scraped_emails:
            _add_record(email, source)

        # WHOIS emails tagged as 'whois' — lower confidence, still included
        for email in whois_emails:
            _add_record(email, "whois")

        return records

    # Requests are I/O-bound; process independent result URLs concurrently.
    # Shared result deduplication is committed in the main thread below.
    executor = ThreadPoolExecutor(max_workers=6)
    futures = [executor.submit(_process_result, result) for result in deduped_results]
    try:
        for future in futures:
            try:
                for record in future.result(timeout=_URL_TIMEOUT_SECONDS):
                    email = record["email"]
                    if email not in seen_emails:
                        seen_emails.add(email)
                        buyer_records.append(record)
            except Exception as e:
                logger.error(f"[Extraction] URL timed out or worker failed: {e}")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    logger.info(f"[Extraction] total buyer records: {len(buyer_records)}")
    return buyer_records


def run_extraction(raw_results: list[dict]) -> list[dict]:
    """Main function to run email extraction (alias kept for backward compatibility)."""
    logger.info("[Extraction] starting email extraction from raw results…")
    records = extract_emails_from_results(raw_results)
    logger.info(f"[Extraction] complete — {len(records)} records.")
    return records


def extract_page_text_public(url: str, use_selenium: bool = False) -> str:
    """Public alias for extract_page_text (used by tests / other modules)."""
    return extract_page_text(url, use_selenium=use_selenium)
