import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_facebook_search():
    """Stub for Facebook search - requires manual cookies or API access."""
    logger.warning(
        "Facebook search requires manual cookies or API access. This source is not implemented."
    )
    logger.warning("To enable Facebook search, you need to:")
    logger.warning("1. Set up Facebook Graph API credentials")
    logger.warning("2. Or provide session cookies for scraping")
    return []
