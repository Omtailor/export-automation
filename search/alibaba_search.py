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


def search_alibaba_buyers():
    """Search Alibaba for buyer inquiry pages."""
    keyword = PRODUCT_TERM.replace(" ", "+")

    # Search for buyer pages on Alibaba
    search_urls = [
        f"https://www.alibaba.com/trade/search?fsb=y&IndexArea=product_en&CatId=&SearchText={keyword}",
        f"https://www.made-in-china.com/products-search/hot-china-products/{keyword}.html",
    ]

    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    for url in search_urls:
        try:
            logger.info(f"Searching Alibaba/Made-in-China: {url}")
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # Extract product/company links
            links = soup.find_all("a", href=True)

            for link in links[: MAX_RESULTS_PER_SOURCE // len(search_urls)]:
                href = link.get("href", "")
                text = link.get_text(strip=True)

                # Look for contact or company pages
                if any(
                    keyword in href.lower()
                    for keyword in ["contact", "company", "supplier", "buyer"]
                ):
                    full_url = (
                        href
                        if href.startswith("http")
                        else f"https://www.alibaba.com{href}"
                    )

                    results.append(
                        {
                            "title": text or "Alibaba Supplier/Buyer",
                            "url": full_url,
                            "snippet": f"Found on Alibaba/Made-in-China marketplace",
                            "source": "alibaba",
                        }
                    )

            time.sleep(1)

        except requests.RequestException as e:
            logger.error(f"Alibaba search failed: {e}")
            continue

    logger.info(f"Alibaba search returned {len(results)} results")
    return results


def run_alibaba_search():
    """Main function to run Alibaba search."""
    return search_alibaba_buyers()
