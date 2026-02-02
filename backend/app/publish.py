"""
LangGraph publishing nodes — delegate to MCP only.

No platform API calls. No OAuth/token access. State and routing only; execution via MCP.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.logging import get_logger
from app.state import AgentState, now_iso

logger = get_logger(__name__)


def _image_is_from_scrape(state: AgentState, url: str) -> bool:
    imgs = (state.get("scraped_content") or {}).get("images") or []
    for im in imgs:
        src = (im.get("src") or im.get("url") or "") if isinstance(im, dict) else (im if isinstance(im, str) else "")
        if src and str(src).strip() == url.strip():
            return True
    return False


def _origin_referer(url: str) -> str:
    """Return scheme + netloc for use as Referer when article referer gets 403."""
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}/"
    except Exception:
        pass
    return ""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
)
async def _download_bytes(url: str, referer: str | None = None) -> bytes:
    """Download image bytes. Tries article referer first, then image origin (CDNs often allow same-origin)."""
    origin_ref = _origin_referer(url)
    headers_base = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    last_response = None
    for ref in (referer, origin_ref):
        headers = {**headers_base, "Referer": ref} if ref else headers_base.copy()
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, follow_redirects=True, headers=headers)
            last_response = r
            if r.status_code == 403 and ref != origin_ref:
                continue  # retry with image-origin referer
            r.raise_for_status()
            return r.content
    if last_response is not None:
        last_response.raise_for_status()
    raise RuntimeError("Image download failed (403)")


async def upload_image(state: AgentState) -> AgentState:
    """
    Upload image ONLY after explicit human approval (enforced by routing).
    Delegates to MCP upload_media for Twitter and LinkedIn.
    """
    if state.get("terminated"):
        return state

    img = state.get("image_metadata") or {}
    image_url = img.get("image_url") or ""
    if not image_url:
        state.setdefault("media_ids", {})
        state["updated_at"] = now_iso()
        return state

    if not _image_is_from_scrape(state, image_url):
        logger.warning("Blocked image upload: URL not in scraped image set.")
        state.setdefault("media_ids", {})
        state["updated_at"] = now_iso()
        return state

    # Use stored bytes from select_image when present; else download with Referer
    blob: bytes | None = None
    if img.get("image_base64"):
        try:
            blob = base64.b64decode(img["image_base64"])
        except Exception:
            pass
    if not blob:
        article_url = (state.get("url") or "").strip()
        try:
            blob = await _download_bytes(image_url, referer=article_url or None)
        except Exception as e:
            logger.warning("Image download failed (%s), publishing text-only: %s", image_url[:60], e)
            state.setdefault("media_ids", {})
            state["updated_at"] = now_iso()
            return state

    state.setdefault("media_ids", {})
    user_id = (state.get("user_id") or "").strip()
    if not user_id:
        state["updated_at"] = now_iso()
        return state

    from mcp_publish.client import call_upload_media

    media_b64 = base64.b64encode(blob).decode("ascii")
    cid_tw = state.get("twitter_connection_id")
    cid_li = state.get("linkedin_connection_id")
    if cid_tw is not None:
        cid_tw = int(cid_tw)
    if cid_li is not None:
        cid_li = int(cid_li)

    try:
        res_tw = await call_upload_media("twitter", media_b64, user_id, cid_tw, image_url)
        if res_tw.get("media_id"):
            state["media_ids"]["twitter_media_id"] = res_tw["media_id"]
        elif res_tw.get("error"):
            logger.warning("Twitter image upload failed: %s", res_tw["error"][:200])
    except Exception:
        logger.exception("Twitter image upload error")

    try:
        res_li = await call_upload_media("linkedin", media_b64, user_id, cid_li, image_url)
        if res_li.get("media_id"):
            state["media_ids"]["linkedin_asset_urn"] = res_li["media_id"]
        elif res_li.get("error"):
            logger.warning("LinkedIn image upload failed: %s", res_li["error"][:200])
    except Exception:
        logger.exception("LinkedIn image upload error")

    state["updated_at"] = now_iso()
    return state


def _connection_id(state: AgentState, key: str) -> int | None:
    v = state.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _user_friendly_error(platform: str, err: str) -> str:
    if not err:
        return f"{platform} publish failed."
    if "402" in err or "CreditsDepleted" in err:
        return "Twitter API credits depleted. Add credits in your X Developer account (developer.x.com) or try again later."
    if "401" in err:
        return f"{platform} token expired. In Connections, disconnect and add again to get a fresh token, then try publishing again."
    if "No connection" in err or "Missing" in err:
        return err
    return err[:300]


async def publish_twitter(state: AgentState) -> AgentState:
    """Publish to Twitter via MCP only. No API or token access here."""
    if state.get("terminated"):
        return state

    state.setdefault("publish_status", {"twitter": "not_started", "linkedin": "not_started"})
    if state["publish_status"].get("twitter") == "published":
        return state

    text = (state.get("approved_twitter_post") or "").strip()
    if not text:
        state["publish_status"]["twitter"] = "skipped"
        state["updated_at"] = now_iso()
        return state

    user_id = (state.get("user_id") or "").strip()
    if not user_id:
        state["publish_status"]["twitter"] = "failed"
        state["publish_status"]["last_error"] = "Missing user_id."
        state["updated_at"] = now_iso()
        return state

    connection_id = _connection_id(state, "twitter_connection_id")
    media_id = (state.get("media_ids") or {}).get("twitter_media_id")

    from mcp_publish.client import call_publish_post

    try:
        result = await call_publish_post("twitter", text, user_id, connection_id, media_id, None)
        status = result.get("status") or "failure"
        if status == "success":
            state["publish_status"]["twitter"] = "published"
            if result.get("post_id"):
                state["publish_status"]["tweet_id"] = result["post_id"]
        else:
            state["publish_status"]["twitter"] = "failed"
            state["publish_status"]["last_error"] = _user_friendly_error("Twitter", result.get("error") or "")
            if "402" in (result.get("error") or "") or "CreditsDepleted" in (result.get("error") or ""):
                logger.warning("Twitter publish: 402 CreditsDepleted")
            else:
                logger.warning("Twitter publish failed: %s", result.get("error", "")[:200])
    except Exception as e:
        state["publish_status"]["twitter"] = "failed"
        state["publish_status"]["last_error"] = _user_friendly_error("Twitter", str(e))
        logger.exception("Twitter publish error")

    state["updated_at"] = now_iso()
    return state


async def publish_linkedin(state: AgentState) -> AgentState:
    """Publish to LinkedIn via MCP only. No API or token access here."""
    if state.get("terminated"):
        return state

    state.setdefault("publish_status", {"twitter": "not_started", "linkedin": "not_started"})
    if state["publish_status"].get("linkedin") == "published":
        return state

    text = (state.get("approved_linkedin_post") or "").strip()
    if not text:
        state["publish_status"]["linkedin"] = "skipped"
        state["updated_at"] = now_iso()
        return state

    user_id = (state.get("user_id") or "").strip()
    if not user_id:
        state["publish_status"]["linkedin"] = "failed"
        state["publish_status"]["last_error"] = "Missing user_id."
        state["updated_at"] = now_iso()
        return state

    connection_id = _connection_id(state, "linkedin_connection_id")
    metadata: dict[str, Any] = {}
    asset = (state.get("media_ids") or {}).get("linkedin_asset_urn")
    if asset:
        metadata["linkedin_asset_urn"] = asset

    from mcp_publish.client import call_publish_post

    try:
        result = await call_publish_post("linkedin", text, user_id, connection_id, None, metadata)
        status = result.get("status") or "failure"
        if status == "success":
            state["publish_status"]["linkedin"] = "published"
            if result.get("post_id"):
                state["publish_status"]["linkedin_post_urn"] = result["post_id"]
        else:
            state["publish_status"]["linkedin"] = "failed"
            state["publish_status"]["last_error"] = _user_friendly_error("LinkedIn", result.get("error") or "")
            logger.warning("LinkedIn publish failed: %s", result.get("error", "")[:200])
    except Exception as e:
        state["publish_status"]["linkedin"] = "failed"
        state["publish_status"]["last_error"] = _user_friendly_error("LinkedIn", str(e))
        logger.exception("LinkedIn publish error")

    state["updated_at"] = now_iso()
    return state
