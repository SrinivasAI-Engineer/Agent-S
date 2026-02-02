from __future__ import annotations

from datetime import datetime, timezone

from langgraph.types import interrupt

from app.db import get_default_connection_expiry, get_default_connection_tokens
from app.state import AgentState, now_iso


def _is_expired(dt: datetime | None) -> bool:
    if dt is None:
        return False
    return dt.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)


async def check_authentication(state: AgentState) -> AgentState:
    """
    Check that the user has at least one valid (connected, non-expired) Twitter and LinkedIn
    connection (from social_connections). If missing/expired, interrupt for re-auth.
    """
    if state.get("terminated"):
        return state

    user_id = state.get("user_id") or ""
    if not user_id:
        state["terminated"] = True
        state["terminate_reason"] = "Missing user_id for auth check."
        state["updated_at"] = now_iso()
        return state

    tw = get_default_connection_tokens(user_id, "twitter")
    li = get_default_connection_tokens(user_id, "linkedin")
    tw_exp = get_default_connection_expiry(user_id, "twitter")
    li_exp = get_default_connection_expiry(user_id, "linkedin")
    tw_ok = tw is not None and not _is_expired(tw_exp)
    li_ok = li is not None and not _is_expired(li_exp)

    needs = []
    if not tw_ok:
        needs.append("twitter")
    if not li_ok:
        needs.append("linkedin")

    state["auth_tokens"] = {
        "twitter_present": tw_ok,
        "twitter_expires_at": tw_exp.isoformat() if tw_exp else None,
        "linkedin_present": li_ok,
        "linkedin_expires_at": li_exp.isoformat() if li_exp else None,
    }

    if needs:
        payload = {
            "type": "reauth_required",
            "execution_id": state.get("execution_id"),
            "user_id": user_id,
            "needs": needs,
            "message": "Authentication required before publishing. Connect Twitter/LinkedIn, then resume.",
        }
        _ = interrupt(payload)

        # After resume, re-check
        tw2 = get_default_connection_tokens(user_id, "twitter")
        li2 = get_default_connection_tokens(user_id, "linkedin")
        tw_exp2 = get_default_connection_expiry(user_id, "twitter")
        li_exp2 = get_default_connection_expiry(user_id, "linkedin")
        if tw2 is None or li2 is None or _is_expired(tw_exp2) or _is_expired(li_exp2):
            state["terminated"] = True
            state["terminate_reason"] = "Authentication not completed."

    state["updated_at"] = now_iso()
    return state

