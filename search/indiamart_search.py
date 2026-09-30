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


def search_indiamart_rfqs():
    """Search IndiaMART for active buyer RFQs (Request for Quotations)."""
    keyword = PRODUCT_TERM.replace(" ", "+")

    search_urls = [
        f"https://www.indiamart.com/search.mp?ss={keyword}",
        f"https://www.tradeindia.com/Seller-Buy-Leads/{keyword}.html",
        f"https://www.exporthub.com/buy/{keyword}.html",
    ]

    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    for url in search_urls:
        try:
            logger.info(f"Searching B2B marketplace: {url}")
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # Extract company/buyer links
            links = soup.find_all("a", href=True)

            for link in links[: MAX_RESULTS_PER_SOURCE // len(search_urls)]:
                href = link.get("href", "")
                text = link.get_text(strip=True)

                # Look for buyer leads, RFQs, or contact pages
                if any(
                    keyword in href.lower()
                    for keyword in ["buyer", "buy-lead", "rfq", "contact", "company"]
                ):
                    full_url = (
                        href
                        if href.startswith("http")
                        else f"https://www.indiamart.com{href}"
                    )

                    results.append(
                        {
                            "title": text or "B2B Buyer Lead",
                            "url": full_url,
                            "snippet": f"Active buyer RFQ or company page",
                            "source": "indiamart",
                        }
                    )

            time.sleep(1)

        except requests.RequestException as e:
            logger.error(f"B2B marketplace search failed for {url}: {e}")
            continue

    logger.info(f"IndiaMART/TradeIndia search returned {len(results)} results")
    return results


def run_indiamart_search():
    """Main function to run IndiaMART/B2B marketplace search."""
    return search_indiamart_rfqs()
