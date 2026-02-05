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


def _is_local_or_unusable(url: str) -> bool:
    """True if URL is localhost/127.0.0.1 or otherwise not fetchable from the server."""
    if not url or not url.strip():
        return True
    try:
        p = urlparse(url.strip())
        netloc = (p.netloc or "").lower().split(":")[0]
        if netloc in ("localhost", "127.0.0.1", "::1"):
            return True
        return False
    except Exception:
        return True


def _ordered_candidates(images: list[dict], article_url: str, og: str) -> list[dict]:
    """Return images in preference order: og:image match first, then by same-site and size."""
    if not images:
        return []
    if og:
        for im in images:
            if (im.get("src") or "") == og:
                return [im] + [i for i in images if i is not im]
    def score(im: dict) -> tuple[int, int]:
        src = str(im.get("src") or "")
        same = 1 if _same_site(article_url, src) else 0
        w = int(im.get("width") or 0) if im.get("width") is not None and str(im.get("width")).isdigit() else 0
        h = int(im.get("height") or 0) if im.get("height") is not None and str(im.get("height")).isdigit() else 0
        return (same, w * h)
    return sorted(images, key=score, reverse=True)


async def select_image(state: AgentState) -> AgentState:
    """
    STRICT RULE: choose only from images extracted from the same article/blog.

    We only consider FireCrawl-extracted images. We prefer same-domain images.
    If the preferred image is localhost or undownloadable, we try the next candidate
    from the scraped list until one succeeds or the list is exhausted.
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

    meta_dict = scraped.get("metadata") or {}
    og = (meta_dict.get("og:image") or meta_dict.get("twitter:image") or "").strip()
    candidates = _ordered_candidates(images, article_url, og)

    state["image_metadata"] = {}
    referer = article_url.strip() or None
    for chosen in candidates:
        src = str(chosen.get("src") or chosen.get("url") or "")
        if _is_local_or_unusable(src):
            continue
        caption = str(chosen.get("alt") or chosen.get("caption") or "")
        try:
            from app.publish import _download_bytes
            blob = await _download_bytes(src, referer=referer)
            if blob:
                state["image_metadata"] = {
                    "image_url": src,
                    "caption": caption,
                    "source": "firecrawl",
                    "image_base64": base64.b64encode(blob).decode("ascii"),
                }
                break
        except Exception:
            continue

    state["updated_at"] = now_iso()
    return state

