"""
MCP Publishing Server — modular publishing for Twitter, LinkedIn.

Tools:
  - publish_post(platform, text, user_id, connection_id?, media_id?, ...)
  - upload_media(platform, media_base64, user_id, connection_id?, image_url?)

Credentials: read from shared DB (SocialConnection). OAuth stays in backend; this server only uses tokens to call platform APIs.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any, Literal

# Add parent so we can import app (shared DB and config)
_backend = Path(__file__).resolve().parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# Optional: only load app if running as server (not when imported for docs)
def _get_tools_impl():
    from app.config import settings
    from app.db import get_connection_tokens, get_default_connection_id, get_default_connection_tokens, update_connection_tokens
    import httpx
    from datetime import datetime, timedelta, timezone

    Platform = Literal["twitter", "linkedin"]

    def _twitter_basic_auth() -> str:
        raw = f"{settings.twitter_client_id}:{settings.twitter_client_secret}".encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    async def _refresh_twitter(connection_id: int, user_id: str, tokens: dict[str, Any]) -> dict[str, Any] | None:
        r = tokens.get("refresh_token") or ""
        if not r or not settings.twitter_client_id or not settings.twitter_client_secret:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.twitter.com/2/oauth2/token",
                headers={"Authorization": f"Basic {_twitter_basic_auth()}", "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "refresh_token", "refresh_token": r, "client_id": settings.twitter_client_id},
            )
            if resp.status_code >= 400:
                return None
            tok = resp.json()
        expires_in = int(tok.get("expires_in") or 0)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None
        new_tokens = {**tokens, **tok}
        if not update_connection_tokens(connection_id, user_id, new_tokens, expires_at):
            return None
        return new_tokens

    async def _ensure_linkedin_person_urn(
        connection_id: int | None, user_id: str, tokens: dict[str, Any]
    ) -> dict[str, Any] | None:
        """If tokens have access_token but no person_urn, fetch from LinkedIn /v2/me and persist."""
        if tokens.get("person_urn"):
            return tokens
        access_token = tokens.get("access_token")
        if not access_token:
            return None
        # OpenID Connect: use userinfo endpoint; user id is in "sub"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if r.status_code >= 400:
                return None
            j = r.json()
            sub = j.get("sub")
            if not sub:
                return None
            person_urn = f"urn:li:person:{sub}"
            new_tokens = {**tokens, "person_urn": person_urn}
            if connection_id is not None:
                update_connection_tokens(connection_id, user_id, new_tokens)
            return new_tokens

    async def publish_post(
        platform: Platform,
        text: str,
        user_id: str,
        connection_id: int | None = None,
        media_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Publish a post to the given platform. Returns { post_id, status }."""
        metadata = metadata or {}
        if connection_id is None:
            connection_id = get_default_connection_id(user_id, platform)
        tokens = (
            get_connection_tokens(connection_id)
            if connection_id is not None
            else get_default_connection_tokens(user_id, platform)
        ) or {}
        if not tokens:
            if platform == "linkedin":
                return {"post_id": "", "status": "failure", "error": "LinkedIn not connected. Connect your account in Settings."}
            return {"post_id": "", "status": "failure", "error": "No connection or tokens"}

        if platform == "twitter":
            access_token = tokens.get("access_token")
            if not access_token:
                return {"post_id": "", "status": "failure", "error": "Missing Twitter access token"}
            payload: dict[str, Any] = {"text": (text or "").strip()}
            if media_id:
                payload["media"] = {"media_ids": [media_id]}
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://api.twitter.com/2/tweets",
                    headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                    json=payload,
                )
                if r.status_code == 401 and connection_id:
                    new_t = await _refresh_twitter(connection_id, user_id, tokens)
                    if new_t and new_t.get("access_token"):
                        r = await client.post(
                            "https://api.twitter.com/2/tweets",
                            headers={"Authorization": f"Bearer {new_t['access_token']}", "Content-Type": "application/json"},
                            json=payload,
                        )
                if r.status_code >= 400:
                    return {"post_id": "", "status": "failure", "error": f"{r.status_code} {r.text[:200]}"}
                data = r.json()
                post_id = str((data.get("data") or {}).get("id") or "")
                return {"post_id": post_id, "status": "success"}

        if platform == "linkedin":
            access_token = tokens.get("access_token")
            if not access_token:
                return {"post_id": "", "status": "failure", "error": "LinkedIn not connected. Connect your account in Settings."}
            author = tokens.get("person_urn")
            if not author:
                tokens = await _ensure_linkedin_person_urn(connection_id, user_id, tokens)
                author = (tokens or {}).get("person_urn")
            if not author:
                return {"post_id": "", "status": "failure", "error": "Could not get LinkedIn profile. Reconnect LinkedIn in Settings."}
            asset = metadata.get("linkedin_asset_urn") or ""
            share = {
                "author": author,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": (text or "").strip()},
                        "shareMediaCategory": "IMAGE" if asset else "NONE",
                    }
                },
                "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
            }
            if asset:
                share["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [
                    {"status": "READY", "description": {"text": ""}, "media": asset, "title": {"text": ""}}
                ]
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://api.linkedin.com/v2/ugcPosts",
                    headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "X-Restli-Protocol-Version": "2.0.0"},
                    json=share,
                )
                if r.status_code >= 400:
                    return {"post_id": "", "status": "failure", "error": f"{r.status_code} {r.text[:200]}"}
                post_id = r.headers.get("x-restli-id") or ""
                return {"post_id": post_id, "status": "success"}

        return {"post_id": "", "status": "failure", "error": f"Unknown platform: {platform}"}

    async def upload_media(
        platform: Platform,
        media_base64: str,
        user_id: str,
        connection_id: int | None = None,
        image_url: str | None = None,
    ) -> dict[str, Any]:
        """Upload media for a platform. Returns { media_id } (e.g. twitter_media_id, linkedin_asset_urn)."""
        try:
            raw = base64.b64decode(media_base64)
        except Exception as e:
            return {"media_id": "", "error": str(e)}
        if not raw:
            return {"media_id": "", "error": "Empty media"}
        if connection_id is None:
            connection_id = get_default_connection_id(user_id, platform)
        tokens = (
            get_connection_tokens(connection_id)
            if connection_id is not None
            else get_default_connection_tokens(user_id, platform)
        ) or {}
        if not tokens:
            if platform == "linkedin":
                return {"media_id": "", "error": "LinkedIn not connected. Connect your account in Settings."}
            return {"media_id": "", "error": "No connection or tokens"}

        if platform == "twitter":
            access_token = tokens.get("access_token")
            if not access_token:
                return {"media_id": "", "error": "Missing Twitter access token"}
            async with httpx.AsyncClient(timeout=45) as client:
                r = await client.post(
                    "https://upload.twitter.com/1.1/media/upload.json",
                    headers={"Authorization": f"Bearer {access_token}"},
                    files={"media": ("image.jpg", raw)},
                )
                if r.status_code >= 400:
                    return {"media_id": "", "error": f"{r.status_code} {r.text[:200]}"}
                mid = str(r.json().get("media_id_string") or r.json().get("media_id") or "")
                return {"media_id": mid}

        if platform == "linkedin":
            access_token = tokens.get("access_token")
            if not access_token:
                return {"media_id": "", "error": "LinkedIn not connected. Connect your account in Settings."}
            owner = tokens.get("person_urn")
            if not owner:
                tokens = await _ensure_linkedin_person_urn(connection_id, user_id, tokens)
                owner = (tokens or {}).get("person_urn")
            if not owner:
                return {"media_id": "", "error": "Could not get LinkedIn profile. Reconnect LinkedIn in Settings."}
            async with httpx.AsyncClient(timeout=45) as client:
                reg = await client.post(
                    "https://api.linkedin.com/v2/assets?action=registerUpload",
                    headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "X-Restli-Protocol-Version": "2.0.0"},
                    json={
                        "registerUploadRequest": {
                            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                            "owner": owner,
                            "serviceRelationships": [{"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}],
                        }
                    },
                )
                if reg.status_code >= 400:
                    return {"media_id": "", "error": f"{reg.status_code} {reg.text[:200]}"}
                reg_json = reg.json()
                asset = reg_json.get("value", {}).get("asset")
                upload_url = (reg_json.get("value", {}).get("uploadMechanism") or {}).get("com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest", {}).get("uploadUrl")
                if not asset or not upload_url:
                    return {"media_id": "", "error": "Missing asset or uploadUrl"}
                up = await client.put(upload_url, content=raw, headers={"Authorization": f"Bearer {access_token}"})
                if up.status_code >= 400:
                    return {"media_id": "", "error": f"Upload {up.status_code} {up.text[:200]}"}
                return {"media_id": str(asset)}

        return {"media_id": "", "error": f"upload_media not implemented for {platform}"}

    return publish_post, upload_media


def run() -> None:
    """Run the MCP server (Streamable HTTP)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print("Install MCP SDK: pip install mcp", file=sys.stderr)
        sys.exit(1)

    mcp = FastMCP("AgentSocialS Publishing", json_response=True)
    publish_post, upload_media = _get_tools_impl()

    @mcp.tool()
    async def publish_post_tool(
        platform: str,
        text: str,
        user_id: str,
        connection_id: int | None = None,
        media_id: str | None = None,
        metadata: str | None = None,
    ) -> str:
        """Publish a post to twitter or linkedin. Returns JSON: { post_id, status [, error ] }."""
        meta = json.loads(metadata) if metadata else {}
        result = await publish_post(platform, text, user_id, connection_id, media_id, meta)
        return json.dumps(result)

    @mcp.tool()
    async def upload_media_tool(
        platform: str,
        media_base64: str,
        user_id: str,
        connection_id: int | None = None,
        image_url: str | None = None,
    ) -> str:
        """Upload media for twitter or linkedin. Returns JSON: { media_id [, error ] }."""
        result = await upload_media(platform, media_base64, user_id, connection_id, image_url)
        return json.dumps(result)

    mcp.run(transport="streamable-http", host="0.0.0.0", port=8001)


if __name__ == "__main__":
    run()
