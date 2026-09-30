import requests
from bs4 import BeautifulSoup
import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import PRODUCT_TERM, MAX_RESULTS_PER_SOURCE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DIRECTORY_SOURCES = {
    "alibaba": "https://www.alibaba.com",
    "kompass": "https://in.kompass.com",
    "tradeindia": "https://www.tradeindia.com",
}


def search_alibaba():
    """Search Alibaba.com for decorative glassware buyers."""
    query = PRODUCT_TERM.replace(" ", "+")
    url = f"https://www.alibaba.com/trade/search?SearchText={query}&f=0"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        results = []
        listings = soup.find_all("div", class_="organic-offer-wrapper")

        for listing in listings[:MAX_RESULTS_PER_SOURCE]:
            title_elem = listing.find("h4")
            link_elem = listing.find("a")
            snippet_elem = listing.find("p", class_="elements-title-normal")

            if title_elem and link_elem:
                results.append(
                    {
                        "title": title_elem.get_text(strip=True),
                        "url": link_elem.get("href"),
                        "snippet": (
                            snippet_elem.get_text(strip=True) if snippet_elem else ""
                        ),
                        "source": "alibaba",
                    }
                )

        logger.info(f"Alibaba returned {len(results)} results")
        return results
    except requests.RequestException as e:
        logger.error(f"Alibaba search failed: {e}")
        return []


def search_kompass():
    """Search Kompass.com for decorative glassware buyers."""
    query = PRODUCT_TERM.replace(" ", "+")
    url = f"https://in.kompass.com/search/en/?text={query}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        results = []
        listings = soup.find_all("div", class_="company-card")

        for listing in listings[:MAX_RESULTS_PER_SOURCE]:
            title_elem = listing.find("h3")
            link_elem = listing.find("a")
            snippet_elem = listing.find("p", class_="description")

            if title_elem and link_elem:
                results.append(
                    {
                        "title": title_elem.get_text(strip=True),
                        "url": link_elem.get("href"),
                        "snippet": (
                            snippet_elem.get_text(strip=True) if snippet_elem else ""
                        ),
                        "source": "kompass",
                    }
                )

        logger.info(f"Kompass returned {len(results)} results")
        return results
    except requests.RequestException as e:
        logger.error(f"Kompass search failed: {e}")
        return []


def search_tradeindia():
    """Search TradeIndia.com for decorative glassware buyers."""
    query = PRODUCT_TERM.replace(" ", "+")
    url = f"https://www.tradeindia.com/search.html?searchword={query}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        results = []
        listings = soup.find_all("div", class_="product-listing")

        for listing in listings[:MAX_RESULTS_PER_SOURCE]:
            title_elem = listing.find("h2")
            link_elem = listing.find("a")
            snippet_elem = listing.find("p", class_="desc")

            if title_elem and link_elem:
                results.append(
                    {
                        "title": title_elem.get_text(strip=True),
                        "url": link_elem.get("href"),
                        "snippet": (
                            snippet_elem.get_text(strip=True) if snippet_elem else ""
                        ),
                        "source": "tradeindia",
                    }
                )

        logger.info(f"TradeIndia returned {len(results)} results")
        return results
    except requests.RequestException as e:
        logger.error(f"TradeIndia search failed: {e}")
        return []


def run_directory_search():
    """Main function to run all directory searches."""
    all_results = []

    # Alibaba has its own configured adapter; keep directory searches focused
    # on the other directory sources to avoid duplicate requests/results.
    all_results.extend(search_kompass())
    all_results.extend(search_tradeindia())

    return all_results
