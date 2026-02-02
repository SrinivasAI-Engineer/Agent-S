from __future__ import annotations

import re

from app.clients.firecrawl import FireCrawlError, scrape_article
from app.logging import get_logger
from app.state import AgentState, now_iso

logger = get_logger(__name__)

# Markdown image syntax: ![alt](url)
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)", re.IGNORECASE)


def _extract_images_from_markdown(markdown: str) -> list[dict]:
    """Extract image URLs and alt text from markdown ![](url) syntax (FireCrawl v2 embeds images here)."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in _MD_IMAGE_RE.finditer(markdown):
        alt, url = m.group(1).strip(), (m.group(2) or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({"src": url, "alt": alt or ""})
    return out


def _normalize_image_item(item: object) -> dict | None:
    """Ensure each image is a dict with at least 'src' or 'url'."""
    if isinstance(item, dict):
        src = item.get("src") or item.get("url") or ""
        if src:
            return {
                "src": str(src),
                "alt": str(item.get("alt") or item.get("caption") or ""),
                "width": item.get("width"),
                "height": item.get("height"),
            }
        return None
    if isinstance(item, str) and item.strip():
        return {"src": item.strip(), "alt": ""}
    return None


async def scrape_content(state: AgentState) -> AgentState:
    if state.get("terminated"):
        return state

    url = state["url"]
    logger.info(f"Starting scrape for url={url}, execution_id={state.get('execution_id')}")
    try:
        data = await scrape_article(url)
        logger.info(f"Scrape completed for url={url}")
    except FireCrawlError as e:
        logger.exception("Scrape failed")
        state["terminated"] = True
        state["terminate_reason"] = f"Scrape failed: {e}"
        state["updated_at"] = now_iso()
        return state

    markdown = (data.get("markdown") or "").strip()
    html = (data.get("html") or "").strip()
    meta = data.get("metadata") or {}
    title = meta.get("title") or meta.get("og:title") or ""

    # Build images list: FireCrawl v2 often embeds images in markdown as ![](url), not a separate array
    raw_images: list[object] = list(data.get("images") or [])
    if not raw_images and "links" in data:
        links = data.get("links", [])
        raw_images = [link for link in links if isinstance(link, str) and any(ext in link.lower() for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"])]
    if not raw_images and markdown:
        # Extract from markdown: ![alt](url)
        for d in _extract_images_from_markdown(markdown):
            raw_images.append(d)
    # Add og:image / twitter:image from metadata so we have at least one candidate for article cards
    for key in ("og:image", "twitter:image"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            raw_images.append({"src": val.strip(), "alt": ""})
            break

    images: list[dict] = []
    seen_urls: set[str] = set()
    for item in raw_images:
        norm = _normalize_image_item(item)
        if norm and norm.get("src") and norm["src"] not in seen_urls:
            seen_urls.add(norm["src"])
            images.append(norm)
    logger.info(f"Scraped {len(images)} image(s) for url={url}")

    # Heuristic headings list - v2 might not have headings directly
    headings = []
    if "headings" in data:
        for h in (data.get("headings") or []):
            if isinstance(h, str):
                headings.append(h.strip())
            elif isinstance(h, dict) and h.get("text"):
                headings.append(str(h["text"]).strip())

    # Basic article-only guard: must have meaningful text
    text = markdown or html
    text_length = len(text)
    logger.info(f"Scraped content length: {text_length} chars, title: {title}")
    
    if text_length < 600:
        logger.warning(f"Content too short ({text_length} chars), terminating execution_id={state.get('execution_id')}")
        state["terminated"] = True
        state["terminate_reason"] = f"Scraped content too short ({text_length} chars); likely not an article/blog."
        state["updated_at"] = now_iso()
        return state

    state["scraped_content"] = {
        "title": title,
        "url": url,
        "text": markdown if markdown else html,
        "headings": headings,
        "metadata": meta,
        "images": images,
    }
    state["updated_at"] = now_iso()
    return state

