from __future__ import annotations

import base64
from urllib.parse import urlparse

from app.state import AgentState, now_iso


def _same_site(article_url: str, image_url: str) -> bool:
    try:
        a = urlparse(article_url)
        i = urlparse(image_url)
        return a.netloc and i.netloc and a.netloc.lower() == i.netloc.lower()
    except Exception:
        return False


async def select_image(state: AgentState) -> AgentState:
    """
    STRICT RULE: choose only from images extracted from the same article/blog.

    We only consider FireCrawl-extracted images. We also prefer same-domain images to
    reduce risk of selecting CDN offsite assets; however if FireCrawl provides CDN
    URLs (different host), we still accept them as "extracted from article" but mark source.
    """
    if state.get("terminated"):
        return state

    scraped = state.get("scraped_content") or {}
    article_url = state.get("url") or scraped.get("url") or ""
    raw_images = scraped.get("images") or []

    # Normalize: accept dicts with src/url or plain string URLs
    images: list[dict] = []
    for im in raw_images:
        if isinstance(im, dict) and (im.get("src") or im.get("url")):
            images.append({"src": im.get("src") or im.get("url"), "alt": im.get("alt") or im.get("caption") or "", "width": im.get("width"), "height": im.get("height")})
        elif isinstance(im, str) and im.strip():
            images.append({"src": im.strip(), "alt": ""})

    meta = scraped.get("metadata") or {}
    og = (meta.get("og:image") or meta.get("twitter:image") or "").strip()
    chosen = None
    # Prefer explicit og:image / twitter:image from metadata when present in our list
    if og:
        for im in images:
            src = im.get("src") or ""
            if src == og:
                chosen = im
                break

    if not chosen and images:
        # Prefer first same-site image; then by size hints
        def score(im: dict) -> tuple[int, int]:
            src = str(im.get("src") or "")
            same = 1 if _same_site(article_url, src) else 0
            w = int(im.get("width") or 0) if im.get("width") is not None and str(im.get("width")).isdigit() else 0
            h = int(im.get("height") or 0) if im.get("height") is not None and str(im.get("height")).isdigit() else 0
            return (same, w * h)

        chosen = max(images, key=score)

    if chosen:
        src = str(chosen.get("src") or chosen.get("url"))
        caption = str(chosen.get("alt") or chosen.get("caption") or "")
        meta: dict = {"image_url": src, "caption": caption, "source": "firecrawl"}
        # Fetch image bytes now (with article Referer) so publish can use them even if host blocks later
        try:
            from app.publish import _download_bytes
            blob = await _download_bytes(src, referer=article_url.strip() or None)
            if blob:
                meta["image_base64"] = base64.b64encode(blob).decode("ascii")
        except Exception:
            pass  # Keep image_url; publish will try downloading again with Referer
        state["image_metadata"] = meta
    else:
        # No image found; that's OK.
        state["image_metadata"] = {}

    state["updated_at"] = now_iso()
    return state

