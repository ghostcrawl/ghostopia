"""GhostCrawl SDK recipe: scrape one page and print its markdown.

This is a GhostCrawl SDK example, not ghostopia application source.
Install: pip install ghostcrawl==2.3.9
Run: GHOSTCRAWL_API_KEY=gck_live_... python scrape_with_ghostcrawl.py
"""

import os

from ghostcrawl import GhostCrawl


def main() -> None:
    key = os.environ["GHOSTCRAWL_API_KEY"]
    client = GhostCrawl(token=key)
    result = client.scrape(url="https://example.com", format="markdown")
    print(result["status"])
    print(result["markdown"][:500])


if __name__ == "__main__":
    main()
