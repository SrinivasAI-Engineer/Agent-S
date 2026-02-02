"""
MCP Publishing client — in-process call into MCP publishing logic.

LangGraph publish nodes use this; they do NOT call platform APIs or touch tokens.
Credentials and API calls are entirely inside the MCP layer (server.py _get_tools_impl).
"""

from __future__ import annotations

from typing import Any

# Lazy: resolve tools on first use so app is fully loaded
_publish_post_fn = None
_upload_media_fn = None


def _get_tools():
    global _publish_post_fn, _upload_media_fn
    if _publish_post_fn is None or _upload_media_fn is None:
        from mcp_publish.server import _get_tools_impl
        _publish_post_fn, _upload_media_fn = _get_tools_impl()
    return _publish_post_fn, _upload_media_fn


async def call_publish_post(
    platform: str,
    text: str,
    user_id: str,
    connection_id: int | None = None,
    media_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish a post via MCP layer. Returns { post_id, status [, error ] }."""
    publish_post, _ = _get_tools()
    return await publish_post(platform, text, user_id, connection_id, media_id, metadata or {})


async def call_upload_media(
    platform: str,
    media_base64: str,
    user_id: str,
    connection_id: int | None = None,
    image_url: str | None = None,
) -> dict[str, Any]:
    """Upload media via MCP layer. Returns { media_id [, error ] }."""
    _, upload_media = _get_tools()
    return await upload_media(platform, media_base64, user_id, connection_id, image_url)
