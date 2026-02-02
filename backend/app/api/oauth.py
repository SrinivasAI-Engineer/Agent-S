from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from urllib.parse import urlencode, quote

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.config import settings
from app.db import add_connection, pop_oauth_state, store_oauth_state
from app.logging import get_logger

router = APIRouter(prefix="/v1/oauth", tags=["oauth"])
log = get_logger(__name__)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _basic_auth(client_id: str, client_secret: str) -> str:
    """Standard Base64 for HTTP Basic auth (Twitter expects this, not base64url)."""
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


@router.get("/twitter/start")
async def twitter_start(user_id: str = Query(..., min_length=1)):
    if not settings.twitter_client_id or not settings.twitter_redirect_uri:
        log.warning("Twitter OAuth not configured: TWITTER_CLIENT_ID or TWITTER_REDIRECT_URI missing in .env")
        raise HTTPException(
            status_code=503,
            detail="Twitter OAuth not configured. Set TWITTER_CLIENT_ID and TWITTER_REDIRECT_URI (and TWITTER_CLIENT_SECRET) in backend/.env.",
        )

    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    try:
        store_oauth_state(state=state, provider="twitter", user_id=user_id, code_verifier=verifier)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to store OAuth state: {str(e)}")

    params = {
        "response_type": "code",
        "client_id": settings.twitter_client_id,
        "redirect_uri": settings.twitter_redirect_uri,
        "scope": "tweet.read tweet.write users.read offline.access",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"https://twitter.com/i/oauth2/authorize?{urlencode(params)}"
    return RedirectResponse(url)


@router.get("/twitter/callback")
async def twitter_callback(code: str = Query(...), state: str = Query(...)):
    row = pop_oauth_state(state=state, provider="twitter")
    if not row:
        log.warning("Twitter OAuth callback: state not found or already used (state=%s)", state[:16] + "...")
        raise HTTPException(status_code=400, detail="Invalid OAuth state. Try connecting again.")

    verifier = row["code_verifier"]
    user_id = row["user_id"]

    token_url = "https://api.twitter.com/2/oauth2/token"
    basic = _basic_auth(settings.twitter_client_id, settings.twitter_client_secret)
    headers = {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.twitter_redirect_uri,
        "code_verifier": verifier,
        "client_id": settings.twitter_client_id,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(token_url, headers=headers, data=data)
        if r.status_code >= 400:
            log.warning("Twitter token exchange failed: %s %s", r.status_code, r.text[:200])
            raise HTTPException(status_code=400, detail=f"Twitter token exchange failed: {r.text}")
        tok = r.json()

        access_token = tok.get("access_token")
        expires_in = int(tok.get("expires_in") or 0)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None

        # Fetch user id and username for display
        twitter_user_id: Optional[str] = None
        display_name: Optional[str] = None
        if access_token:
            me = await client.get(
                "https://api.twitter.com/2/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"user.fields": "username"},
            )
            if me.status_code < 400:
                me_data = me.json().get("data") or {}
                twitter_user_id = me_data.get("id")
                username = me_data.get("username")
                if username:
                    display_name = f"@{username}"

    tok["twitter_user_id"] = twitter_user_id
    account_id = twitter_user_id or (tok.get("access_token", "")[:16])
    conn = add_connection(
        user_id=user_id,
        provider="twitter",
        account_id=str(account_id),
        display_name=display_name or account_id,
        token_payload=tok,
        expires_at=expires_at,
    )
    has_refresh = "refresh_token" in tok and bool(tok.get("refresh_token"))
    log.info("Twitter connection saved: connection_id=%s, account_id=%s, has_refresh_token=%s", conn.id, account_id, has_refresh)

    return RedirectResponse(f"{settings.frontend_base_url}?oauth=twitter&status=ok")


@router.get("/linkedin/start")
async def linkedin_start(user_id: str = Query(..., min_length=1)):
    if not settings.linkedin_client_id or not settings.linkedin_redirect_uri:
        log.warning("LinkedIn OAuth not configured: LINKEDIN_CLIENT_ID or LINKEDIN_REDIRECT_URI missing in .env")
        raise HTTPException(
            status_code=503,
            detail="LinkedIn OAuth not configured. Set LINKEDIN_CLIENT_ID and LINKEDIN_REDIRECT_URI (and LINKEDIN_CLIENT_SECRET) in backend/.env.",
        )

    state = secrets.token_urlsafe(24)
    try:
        store_oauth_state(state=state, provider="linkedin", user_id=user_id, code_verifier="")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to store OAuth state: {str(e)}")

    # LinkedIn: openid + profile (Sign In) + w_member_social (Share on LinkedIn, required for ugcPosts).
    params = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": settings.linkedin_redirect_uri,
        "scope": "openid profile w_member_social",
        "state": state,
    }
    url = f"https://www.linkedin.com/oauth/v2/authorization?{urlencode(params)}"
    return RedirectResponse(url)


@router.get("/linkedin/callback")
async def linkedin_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    if error:
        log.warning("LinkedIn OAuth error: %s - %s", error, error_description or "")
        msg = (error_description or error).replace("+", " ").strip()
        return RedirectResponse(f"{settings.frontend_base_url}?oauth=linkedin&status=error&message={quote(msg, safe='')}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state from LinkedIn")
    row = pop_oauth_state(state=state, provider="linkedin")
    if not row:
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")
    user_id = row["user_id"]

    token_url = "https://www.linkedin.com/oauth/v2/accessToken"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.linkedin_redirect_uri,
        "client_id": settings.linkedin_client_id,
        "client_secret": settings.linkedin_client_secret,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(token_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code >= 400:
            raise HTTPException(status_code=400, detail=f"LinkedIn token exchange failed: {r.text}")
        tok = r.json()

        access_token = tok.get("access_token")
        expires_in = int(tok.get("expires_in") or 0)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None

        # OpenID Connect: use userinfo endpoint; user id is in "sub", not /v2/me "id"
        person_urn = None
        display_name = None
        if access_token:
            me = await client.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if me.status_code < 400:
                j = me.json()
                sub = j.get("sub")
                if sub:
                    person_urn = f"urn:li:person:{sub}"
                display_name = j.get("name") or (j.get("given_name") or "") + " " + (j.get("family_name") or "").strip() or str(sub)

    tok["person_urn"] = person_urn
    account_id = (person_urn or tok.get("access_token", "")[:16]) if person_urn else tok.get("access_token", "")[:16]
    conn = add_connection(
        user_id=user_id,
        provider="linkedin",
        account_id=account_id,
        display_name=display_name or account_id,
        token_payload=tok,
        expires_at=expires_at,
    )
    log.info("LinkedIn connection saved: connection_id=%s, account_id=%s", conn.id, account_id)

    return RedirectResponse(f"{settings.frontend_base_url}?oauth=linkedin&status=ok")

