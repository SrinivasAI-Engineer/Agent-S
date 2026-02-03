"""
MCP Publishing client — in-process or standalone (remote) MCP server.

When app.config.settings.mcp_publish_url is set, the client calls the standalone
MCP server over HTTP (Streamable HTTP / JSON-RPC). Otherwise it uses in-process
calls into server._get_tools_impl().

LangGraph publish nodes use this; they do NOT call platform APIs or touch tokens.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

# Lazy: resolve tools on first use so app is fully loaded
_publish_post_fn = None
_upload_media_fn = None

# Log which mode we're using once per process
_mode_logged: bool = False


def _log_mode_once() -> None:
    """Log to terminal whether MCP publish is In-process or Standalone (once per process)."""
    global _mode_logged
    if _mode_logged:
        return
    _mode_logged = True
    from app.logging import get_logger
    logger = get_logger(__name__)
    # Resolve URL from config so we can log what we actually see
    from app.config import settings
    raw_url = (getattr(settings, "mcp_publish_url", None) or "").strip()
    base = _mcp_base_url()
    if raw_url and not base:
        logger.warning("MCP publish: mcp_publish_url is set (%r) but resolved base is empty; using In-process", raw_url)
    if base:
        logger.info(
            "MCP publish: Standalone — using remote server at %s (initialize handshake on first call, then publish_post_tool / upload_media_tool via JSON-RPC)",
            base,
        )
    else:
        logger.info(
            "MCP publish: In-process — calling mcp_publish.server tools directly (mcp_publish_url not set)",
        )


def _get_tools():
    global _publish_post_fn, _upload_media_fn
    if _publish_post_fn is None or _upload_media_fn is None:
        from mcp_publish.server import _get_tools_impl
        _publish_post_fn, _upload_media_fn = _get_tools_impl()
    return _publish_post_fn, _upload_media_fn


def _mcp_base_url() -> str | None:
    """Return base URL for standalone MCP server, or None for in-process."""
    from urllib.parse import urlparse
    from app.config import settings
    url = (settings.mcp_publish_url or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    # FastMCP streamable-http often serves at /mcp; if URL has no path, use /mcp
    if not parsed.path or parsed.path == "/":
        return f"{parsed.scheme}://{parsed.netloc}/mcp"
    return url.rstrip("/")


# One-time init sent to remote MCP server (some servers require initialize before tools/call)
_remote_initialized: bool = False
_remote_session_id: str | None = None


def _session_headers() -> dict[str, str]:
    """Headers for MCP Streamable HTTP; include session ID if we have one."""
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if _remote_session_id:
        h["Mcp-Session-Id"] = _remote_session_id
    return h


async def _call_remote_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a tool on the standalone MCP server via JSON-RPC over HTTP."""
    import httpx
    global _remote_initialized, _remote_session_id
    base = _mcp_base_url()
    if not base:
        raise RuntimeError("mcp_publish_url not set")
    async with httpx.AsyncClient(timeout=60) as client:
        # MCP Streamable HTTP: initialize first, then send Mcp-Session-Id on all later requests
        if not _remote_initialized:
            init_payload = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "AgentSocialS-backend", "version": "1.0"},
                },
            }
            ri = await client.post(base, json=init_payload, headers=_session_headers())
            if ri.is_success and "error" not in (ri.json() or {}):
                _remote_initialized = True
                # Capture session ID for subsequent requests (required by MCP Streamable HTTP)
                sid = (
                    ri.headers.get("mcp-session-id")
                    or ri.headers.get("Mcp-Session-Id")
                    or ""
                ).strip() or None
                if sid:
                    _remote_session_id = sid
                # Send Initialized notification
                await client.post(
                    base,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers=_session_headers(),
                )
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        r = await client.post(base, json=payload, headers=_session_headers())
        r.raise_for_status()
        data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    result = data.get("result")
    if not result:
        raise RuntimeError("No result in MCP response")
    # MCP tool result: content list with text parts
    content = result.get("content") or []
    text_parts = [c["text"] for c in content if isinstance(c.get("text"), str)]
    if not text_parts:
        return {}
    raw = text_parts[0]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": raw, "status": "failure"}


async def call_publish_post(
    platform: str,
    text: str,
    user_id: str,
    connection_id: int | None = None,
    media_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish a post via MCP layer. Returns { post_id, status [, error ] }."""
    _log_mode_once()
    base = _mcp_base_url()
    if base:
        return await _call_remote_tool("publish_post_tool", {
            "platform": platform,
            "text": text,
            "user_id": user_id,
            "connection_id": connection_id,
            "media_id": media_id,
            "metadata": json.dumps(metadata or {}),
        })
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
    _log_mode_once()
    base = _mcp_base_url()
    if base:
        return await _call_remote_tool("upload_media_tool", {
            "platform": platform,
            "media_base64": media_base64,
            "user_id": user_id,
            "connection_id": connection_id,
            "image_url": image_url,
        })
    _, upload_media = _get_tools()
    return await upload_media(platform, media_base64, user_id, connection_id, image_url)
