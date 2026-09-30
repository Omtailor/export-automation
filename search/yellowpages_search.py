import requests
from bs4 import BeautifulSoup
import logging
import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import PRODUCT_TERM, MAX_RESULTS_PER_SOURCE, SERPER_API_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def search_yellowpages_yelp():
    """Search Yellow Pages and Yelp for stores selling home decor and glassware."""
    keyword = PRODUCT_TERM

    # Google search for Yellow Pages and Yelp listings
    queries = [
        f'site:yellowpages.com "{keyword}" store',
        f'site:yelp.com "{keyword}" shop',
        f'"home decor" OR "gift shop" store "{keyword}"',
    ]

    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    for query in queries:
        try:
            logger.info(f"Searching: {query}")
            if SERPER_API_KEY:
                response = requests.post(
                    "https://google.serper.dev/search",
                    headers={
                        "X-API-KEY": SERPER_API_KEY,
                        "Content-Type": "application/json",
                    },
                    json={"q": query, "num": MAX_RESULTS_PER_SOURCE},
                    timeout=15,
                )
                response.raise_for_status()
                for item in response.json().get("organic", []):
                    if item.get("link"):
                        results.append(
                            {
                                "title": item.get("title", ""),
                                "url": item["link"],
                                "snippet": item.get("snippet", ""),
                                "source": "yellowpages",
                                "query": query,
                            }
                        )
                continue
            url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            search_divs = soup.find_all("div", class_="g") or soup.select(
                "div[data-snhf]"
            )

            for div in search_divs[: MAX_RESULTS_PER_SOURCE // len(queries)]:
                title_elem = div.find("h3")
                link_elem = div.find("a")
                snippet_elem = div.find("span", class_="st") or div.select_one(
                    "div.VwiC3b"
                )

                if title_elem and link_elem:
                    results.append(
                        {
                            "title": title_elem.get_text(strip=True),
                            "url": link_elem.get("href"),
                            "snippet": (
                                snippet_elem.get_text(strip=True)
                                if snippet_elem
                                else ""
                            ),
                            "source": "yellowpages",
                        }
                    )

            time.sleep(1)

        except requests.RequestException as e:
            logger.error(f"Yellow Pages/Yelp search failed for '{query}': {e}")
            continue

    logger.info(f"Yellow Pages/Yelp search returned {len(results)} results")
    return results


def run_yellowpages_search():
    """Main function to run Yellow Pages/Yelp search."""
    return search_yellowpages_yelp()
