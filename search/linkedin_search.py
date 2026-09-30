import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_linkedin_search():
    """Stub for LinkedIn search - requires manual cookies or API access."""
    logger.warning(
        "LinkedIn search requires manual cookies or API access. This source is not implemented."
    )
    logger.warning("To enable LinkedIn search, you need to:")
    logger.warning("1. Set up LinkedIn API credentials")
    logger.warning("2. Or provide session cookies for scraping")
    return []
