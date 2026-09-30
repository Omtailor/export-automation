import requests
from bs4 import BeautifulSoup
import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import KNOWN_BUYER_SITES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def scrape_page(url):
    """Scrape a single page and return raw text."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        text = soup.get_text(separator=" ", strip=True)
        logger.info(f"Successfully scraped {url}")
        return text
    except requests.RequestException as e:
        logger.error(f"Failed to scrape {url}: {e}")
        return ""


def run_website_search():
    """Visit known buyer sites' contact and about pages."""
    results = []

    for site in KNOWN_BUYER_SITES:
        contact_url = f"{site.rstrip('/')}/contact"
        about_url = f"{site.rstrip('/')}/about"

        contact_text = scrape_page(contact_url)
        about_text = scrape_page(about_url)

        if contact_text:
            results.append(
                {"url": contact_url, "text": contact_text, "source": "website"}
            )

        if about_text:
            results.append({"url": about_url, "text": about_text, "source": "website"})

    logger.info(f"Website search returned {len(results)} results")
    return results
