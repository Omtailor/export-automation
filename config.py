"""
Central configuration for Product Zone International B2B buyer outreach.

Company: Product Zone International — wholesale supplier of decorative
glassware and home decor products.

Workflow implemented by this project:
    SEARCH -> EXTRACT -> VALIDATE -> DEDUPLICATE -> EXCLUDE SENT -> buyers.csv
    -> "Send Campaign" (max 10 per run) -> sent_log.csv -> remove sent from
    buyers.csv
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# Sender — Gmail address for campaign emails
# ──────────────────────────────────────────────
GMAIL_EMAIL = os.getenv("GMAIL_EMAIL")
# Gmail App Password is read from .env (GMAIL_APP_PASSWORD) and never stored here.

# ──────────────────────────────────────────────
# Core search settings
# ──────────────────────────────────────────────
# Product terms used by the single-keyword marketplace adapters
# (Alibaba / IndiaMART / directories / Instagram / Yellow Pages).
PRODUCT_TERM = os.getenv("PRODUCT_TERM", "decorative glassware")
PRODUCT_TERMS = [
    term.strip()
    for term in os.getenv("PRODUCT_TERMS", "decorative glassware,home decor,vases")
    .split(",")
    if term.strip()
]

# ──────────────────────────────────────────────
# All active sources.  Remove a name to disable that source.
# ──────────────────────────────────────────────
SOURCES = [
    "google",
    "facebook",
    "linkedin",
    "directory",
    "website",
    "alibaba",
    "indiamart",
    "instagram",
    "yellowpages",
]

MAX_RESULTS_PER_SOURCE = int(os.getenv("MAX_RESULTS_PER_SOURCE", "20"))

# How many Serper/Google result pages to fetch per query
MAX_SEARCH_PAGES = int(os.getenv("MAX_SEARCH_PAGES", "3"))

# Maximum pages to crawl per domain during extraction
MAX_PAGES_PER_DOMAIN = int(os.getenv("MAX_PAGES_PER_DOMAIN", "10"))

# ──────────────────────────────────────────────
# B2B buyer-intent query set
# One "Send Campaign" click never contacts more than this many buyers.
# ──────────────────────────────────────────────
CAMPAIGN_BATCH_SIZE = int(os.getenv("CAMPAIGN_BATCH_SIZE", "10"))

DEFAULT_SEARCH_QUERIES = [
    # Core product — importer / distributor / wholesaler / buyer
    "decorative glassware importer USA",
    "decorative glassware wholesaler USA",
    "decorative glassware distributor USA",
    "decorative glassware buyer USA",
    # Home decor — importer / distributor / wholesaler / buyer
    "home decor wholesaler USA",
    "home decor importer USA",
    "home decor distributor USA",
    "home decor buyer USA",
    # Glassware (wider)
    "glassware importer USA",
    "glassware wholesaler USA",
    "glassware distributor USA",
    # Glass decor / accessories
    "glass decor wholesaler USA",
    "glass decor importer USA",
    "home accessories wholesaler USA",
    "decorative accessories wholesaler USA",
    # Candle / votive holders
    "votive holders wholesaler USA",
    "tea light holders wholesaler USA",
    "candle holders wholesaler USA",
    "candlestick holders wholesaler USA",
    # Jars
    "decorative jars wholesaler USA",
    "decorative glass jars importer USA",
    # Vases and bottles
    "decorative vase wholesaler USA",
    "glass vase distributor USA",
    "decorative glass bottles wholesaler USA",
    "glass bottle importer USA",
    # Retail / specialty buyer types
    "boutique home decor stores USA",
    "gift shop wholesalers USA",
    "luxury home decor stores USA",
    "wedding decor suppliers USA",
    "event decor companies USA",
]

# Allow overriding/extending the query set from .env (comma separated).
_env_queries = [
    q.strip() for q in os.getenv("SEARCH_QUERIES", "").split(",") if q.strip()
]
SEARCH_QUERIES: list[str] = _env_queries or list(DEFAULT_SEARCH_QUERIES)

# Backwards-compatible alias used by the Google/Serper search adapter.
BUYER_QUERY_VARIATIONS = SEARCH_QUERIES

# ──────────────────────────────────────────────
# Presentation attachment
# ──────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

_presentation = os.getenv(
    "PRESENTATION_PATH", os.path.join("assets", "company_presentation.pdf")
)
# Resolve a relative presentation path against the project root so the PDF is
# still found when the app is started from a different working directory.
PRESENTATION_PATH = (
    _presentation
    if os.path.isabs(_presentation)
    else os.path.normpath(os.path.join(PROJECT_ROOT, _presentation))
)

# ──────────────────────────────────────────────
# Product / company details
# ──────────────────────────────────────────────
PRODUCT_NAME = os.getenv(
    "PRODUCT_NAME", "Decorative Glassware & Home Décor Collection"
)
COMPANY_NAME = os.getenv("COMPANY_NAME", "Product Zone International")

# ──────────────────────────────────────────────
# Email campaign defaults (universal — no per-buyer personalization)
# ──────────────────────────────────────────────
DEFAULT_SUBJECT = os.getenv(
    "DEFAULT_SUBJECT",
    "Wholesale Decorative Glassware & Home Décor Collection",
)

DEFAULT_BODY = os.getenv(
    "DEFAULT_BODY",
    (
        "Dear Sir/Madam,\n\n"
        "We are reaching out from Product Zone International, a wholesale supplier "
        "of decorative glassware and home décor products for retailers, gift shops, "
        "boutiques, and décor businesses.\n\n"
        "Our collection includes decorative jars, votive and tea light holders, "
        "candlestick holders, glass bottles, vases, frosted glass cups, and "
        "decorative home hardware. We offer a wide variety of designs and colors, "
        "with competitive wholesale pricing, low MOQs, tiered pricing, "
        "custom/private-label packaging, and international shipping support.\n\n"
        "We have attached our company presentation PDF with our product range and "
        "wholesale offering.\n\n"
        "If you are interested in sourcing decorative glassware or home décor "
        "products, we would be happy to discuss product selection, pricing, MOQ, "
        "and shipping options.\n\n"
        "Please feel free to reply to this email for further information.\n\n"
        "Best regards,\n"
        "Product Zone International\n"
        "Wholesale Decorative Glassware & Home Décor"
    ),
)

# ──────────────────────────────────────────────
# Known buyer sites (used by website_search)
# ──────────────────────────────────────────────
KNOWN_BUYER_SITES: list[str] = []

# ──────────────────────────────────────────────
# API keys
# ──────────────────────────────────────────────
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

# ──────────────────────────────────────────────
# Email validation settings
# Canonical name: ENABLE_SMTP_VERIFICATION
# (old name ENABLE_SMTP_VERIFY also accepted for backward compatibility)
# ──────────────────────────────────────────────
_smtp_verify_raw = (
    os.getenv("ENABLE_SMTP_VERIFICATION") or os.getenv("ENABLE_SMTP_VERIFY") or "false"
)
ENABLE_SMTP_VERIFICATION: bool = _smtp_verify_raw.lower() == "true"

# ──────────────────────────────────────────────
# Sending limits and delays
# ──────────────────────────────────────────────
DAILY_SEND_LIMIT = int(os.getenv("DAILY_SEND_LIMIT", "100"))
# MIN/MAX send delay in seconds (randomised between them)
MIN_SEND_DELAY = int(os.getenv("MIN_SEND_DELAY", os.getenv("SEND_DELAY", "2")))
MAX_SEND_DELAY = int(os.getenv("MAX_SEND_DELAY", str(MIN_SEND_DELAY + 2)))

# ──────────────────────────────────────────────
# Configuration validation
# ──────────────────────────────────────────────
def _validate_gmail_config():
    """Validate that required Gmail credentials are configured."""
    if not GMAIL_EMAIL:
        raise ValueError(
            "GMAIL_EMAIL is not set in .env file. "
            "Please add GMAIL_EMAIL=your_gmail_address@gmail.com to your .env file."
        )
    if not os.getenv("GMAIL_APP_PASSWORD"):
        raise ValueError(
            "GMAIL_APP_PASSWORD is not set in .env file. "
            "Please add GMAIL_APP_PASSWORD=your_gmail_app_password to your .env file."
        )

_validate_gmail_config()
