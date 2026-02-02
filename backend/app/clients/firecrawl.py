from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings


class FireCrawlError(RuntimeError):
    pass


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
async def scrape_article(url: str) -> dict[str, Any]:
    """
    Scrape an article/blog URL via FireCrawl.

    We request main content + metadata + images. The workflow enforces "article/blog only"
    by checking that meaningful text exists and relevance scoring passes.
    """
    if not settings.firecrawl_api_key:
        raise FireCrawlError("FIRECRAWL_API_KEY is not set.")

    endpoint = f"{settings.firecrawl_api_base.rstrip('/')}/v2/scrape"
    headers = {"Authorization": f"Bearer {settings.firecrawl_api_key}", "Content-Type": "application/json"}
    payload = {
        "url": url,
        "formats": ["markdown", "html"],
        "onlyMainContent": True,
        "includeTags": ["article", "main"],
        "excludeTags": ["nav", "footer", "aside"],
        "waitFor": 1500,
        "timeout": 45000,
        "blockAds": True,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(endpoint, headers=headers, json=payload)
        if r.status_code >= 400:
            raise FireCrawlError(f"FireCrawl scrape failed ({r.status_code}): {r.text}")

        data = r.json()
        # FireCrawl typically wraps in { success, data }
        if not data or (isinstance(data, dict) and data.get("success") is False):
            raise FireCrawlError(f"FireCrawl scrape unsuccessful: {data}")
        return data.get("data", data)

