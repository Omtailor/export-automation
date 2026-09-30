"""
Google search adapter.

Uses Serper.dev API (POST JSON) as primary source.
Falls back to direct Google scraping if Serper is unavailable.

Supports:
- Multiple buyer-intent query variations (from config.BUYER_QUERY_VARIATIONS)
- Configurable pagination (config.MAX_SEARCH_PAGES pages per query)
- URL-level deduplication within a single run
"""

import requests
from bs4 import BeautifulSoup
import logging
import os
import sys
import time
from urllib.parse import urlparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    BUYER_QUERY_VARIATIONS,
    MAX_RESULTS_PER_SOURCE,
    MAX_SEARCH_PAGES,
    SERPER_API_KEY,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# How many results to request per page from Serper
_SERPER_RESULTS_PER_PAGE = 10
# Serper endpoint
_SERPER_URL = "https://google.serper.dev/search"


def _normalise_url(url: str) -> str:
    """Return a normalised URL string for deduplication (scheme+netloc+path, no trailing slash)."""
    try:
        p = urlparse(url)
        normalised = f"{p.scheme}://{p.netloc}{p.path}".rstrip("/").lower()
        return normalised
    except Exception:
        return url.lower().rstrip("/")


def search_serper_api():
    """
    Search via Serper.dev API (POST JSON) with all buyer-intent query variations
    and configurable multi-page pagination.

    Returns list of result dicts: {title, url, snippet, source, query, page}
    Returns None if API key is missing.
    """
    if not SERPER_API_KEY:
        logger.warning("SERPER_API_KEY not set — falling back to Google scraping.")
        return None

    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json",
    }

    all_results: list[dict] = []
    seen_urls: set[str] = set()

    for query in BUYER_QUERY_VARIATIONS:
        for page in range(1, MAX_SEARCH_PAGES + 1):
            payload = {
                "q": query,
                "num": _SERPER_RESULTS_PER_PAGE,
                "page": page,
            }
            try:
                logger.info(f"[Serper] query='{query}' page={page}")
                response = requests.post(
                    _SERPER_URL,
                    headers=headers,
                    json=payload,
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()

                organic = data.get("organic", [])
                new_on_page = 0

                for item in organic:
                    url = item.get("link", "")
                    if not url:
                        continue
                    norm = _normalise_url(url)
                    if norm in seen_urls:
                        logger.debug(f"[Serper] duplicate skipped: {url}")
                        continue
                    seen_urls.add(norm)
                    all_results.append(
                        {
                            "title": item.get("title", ""),
                            "url": url,
                            "snippet": item.get("snippet", ""),
                            "source": "google",
                            "query": query,
                            "page": page,
                        }
                    )
                    new_on_page += 1

                logger.info(
                    f"[Serper] query='{query}' page={page} → "
                    f"{len(organic)} raw / {new_on_page} new unique"
                )

                # If a page returned no results stop paginating this query
                if not organic:
                    break

                # Polite delay between pages
                time.sleep(0.5)

            except requests.HTTPError as e:
                logger.error(
                    f"[Serper] HTTP error for query='{query}' page={page}: {e}"
                )
                break  # stop paginating this query on error
            except requests.RequestException as e:
                logger.error(
                    f"[Serper] request failed for query='{query}' page={page}: {e}"
                )
                break

        # Small delay between queries
        time.sleep(0.3)

    logger.info(
        f"[Serper] total unique results across all queries/pages: {len(all_results)}"
    )
    return all_results


def search_google_scrape():
    """
    Fallback: scrape google.com search results using requests + BeautifulSoup.
    Only used when Serper API is unavailable.
    Uses the first 5 buyer-intent query variations (to avoid rate-limiting).
    """
    all_results: list[dict] = []
    seen_urls: set[str] = set()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    # Limit scraping to first 5 queries to avoid rate-limits
    for query in BUYER_QUERY_VARIATIONS[:5]:
        for page in range(1, min(MAX_SEARCH_PAGES, 2) + 1):  # cap scraping at 2 pages
            start = (page - 1) * 10
            url = (
                f"https://www.google.com/search"
                f"?q={requests.utils.quote(query)}&start={start}&hl=en"
            )
            try:
                logger.info(f"[Scrape] query='{query}' page={page}")
                response = requests.get(url, headers=headers, timeout=12)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

                new_on_page = 0
                # Google renders results inside <div class="g"> containers
                for div in soup.find_all("div", class_="g"):
                    link_elem = div.find("a", href=True)
                    title_elem = div.find("h3")
                    snippet_elem = div.find(
                        "div", attrs={"data-sncf": True}
                    ) or div.find("span", class_="st")

                    if not (link_elem and title_elem):
                        continue

                    href = link_elem["href"]
                    if not href.startswith("http"):
                        continue

                    norm = _normalise_url(href)
                    if norm in seen_urls:
                        continue
                    seen_urls.add(norm)

                    all_results.append(
                        {
                            "title": title_elem.get_text(strip=True),
                            "url": href,
                            "snippet": (
                                snippet_elem.get_text(strip=True)
                                if snippet_elem
                                else ""
                            ),
                            "source": "google",
                            "query": query,
                            "page": page,
                        }
                    )
                    new_on_page += 1

                logger.info(f"[Scrape] page={page} → {new_on_page} new results")
                time.sleep(2)  # Be polite to Google

            except requests.RequestException as e:
                logger.error(f"[Scrape] failed for query='{query}' page={page}: {e}")
                break

    logger.info(f"[Scrape] total unique results: {len(all_results)}")
    return all_results


def run_google_search() -> list[dict]:
    """
    Main entry point for Google search.
    Tries Serper API first; falls back to direct scraping.
    """
    results = search_serper_api()

    if results is None or len(results) == 0:
        logger.info("[Google] Serper returned nothing — switching to scrape fallback.")
        results = search_google_scrape()

    # Ensure every record has the 'source' field
    for r in results:
        r.setdefault("source", "google")

    logger.info(f"[Google] final result count: {len(results)}")
    return results
