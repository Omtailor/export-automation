import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import SOURCES
from .google_search import run_google_search
from .facebook_search import run_facebook_search
from .linkedin_search import run_linkedin_search
from .directory_search import run_directory_search
from .website_search import run_website_search
from .alibaba_search import run_alibaba_search
from .indiamart_search import run_indiamart_search
from .instagram_search import run_instagram_search
from .yellowpages_search import run_yellowpages_search

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_all_sources():
    """Run all search adapters and aggregate results."""
    all_results = []
    source_functions = {
        "google": run_google_search,
        "facebook": run_facebook_search,
        "linkedin": run_linkedin_search,
        "directory": run_directory_search,
        "website": run_website_search,
        "alibaba": run_alibaba_search,
        "indiamart": run_indiamart_search,
        "instagram": run_instagram_search,
        "yellowpages": run_yellowpages_search
    }
    
    for source in SOURCES:
        if source in source_functions:
            try:
                logger.info(f"Running {source} search...")
                results = source_functions[source]()
                all_results.extend(results)
                logger.info(f"{source} search completed: {len(results)} results")
            except Exception as e:
                logger.error(f"Error running {source} search: {e}")
                continue
        else:
            logger.warning(f"Unknown source: {source}")
    
    logger.info(f"Total raw results from all sources: {len(all_results)}")
    return all_results


if __name__ == "__main__":
    results = run_all_sources()
    print(f"\n=== SUMMARY ===")
    print(f"Total results: {len(results)}")
    for i, result in enumerate(results[:5], 1):
        print(f"\nResult {i}:")
        print(f"Title: {result.get('title', 'N/A')}")
        print(f"URL: {result.get('url', 'N/A')}")
        print(f"Snippet: {result.get('snippet', result.get('text', 'N/A'))[:100]}...")
