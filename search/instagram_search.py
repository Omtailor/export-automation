import requests
from bs4 import BeautifulSoup
import logging
import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import PRODUCT_TERM, MAX_RESULTS_PER_SOURCE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def search_instagram_bios():
    """
    Search for Instagram profiles via Google that mention the keyword.
    Instagram bio emails can be extracted later during page scraping.
    """
    keyword = PRODUCT_TERM

    # Google search for Instagram profiles
    query = f'site:instagram.com "{keyword}" email OR contact'
    url = f"https://www.google.com/search?q={query.replace(' ', '+')}"

    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        logger.info(f"Searching Instagram profiles via Google: {query}")
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        search_divs = soup.find_all("div", class_="g")

        for div in search_divs[:MAX_RESULTS_PER_SOURCE]:
            title_elem = div.find("h3")
            link_elem = div.find("a")
            snippet_elem = div.find("span", class_="st")

            if title_elem and link_elem:
                href = link_elem.get("href", "")

                # Only include Instagram profile links
                if "instagram.com" in href:
                    results.append(
                        {
                            "title": title_elem.get_text(strip=True),
                            "url": href,
                            "snippet": (
                                snippet_elem.get_text(strip=True)
                                if snippet_elem
                                else ""
                            ),
                            "source": "instagram",
                        }
                    )

        time.sleep(1)

    except requests.RequestException as e:
        logger.error(f"Instagram search failed: {e}")

    logger.info(f"Instagram search returned {len(results)} results")
    return results


def run_instagram_search():
    """Main function to run Instagram search."""
    return search_instagram_bios()
