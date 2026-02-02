from __future__ import annotations

from typing import Any, Literal

from langgraph.types import interrupt

from app.state import AgentState, now_iso


def _normalize_bool(v: Any) -> bool:
    return bool(v) is True


def apply_hitl_actions(state: AgentState, actions: dict[str, Any]) -> AgentState:
    """
    Apply edits/checkboxes to state.
    Routing happens in graph conditional edges; this function only mutates state.
    """
    state["hitl_actions"] = {
        "approve_content": _normalize_bool(actions.get("approve_content")),
        "reject_content": _normalize_bool(actions.get("reject_content")),
        "approve_image": _normalize_bool(actions.get("approve_image")),
        "reject_image": _normalize_bool(actions.get("reject_image")),
        "regenerate_twitter": _normalize_bool(actions.get("regenerate_twitter")),
        "regenerate_linkedin": _normalize_bool(actions.get("regenerate_linkedin")),
        "edited_twitter": (actions.get("edited_twitter") or "").strip(),
        "edited_linkedin": (actions.get("edited_linkedin") or "").strip(),
        "twitter_connection_id": actions.get("twitter_connection_id"),
        "linkedin_connection_id": actions.get("linkedin_connection_id"),
        "acted_at": now_iso(),
    }
    if actions.get("twitter_connection_id") is not None:
        state["twitter_connection_id"] = actions["twitter_connection_id"]
    if actions.get("linkedin_connection_id") is not None:
        state["linkedin_connection_id"] = actions["linkedin_connection_id"]
    if actions.get("image_base64"):
        state.setdefault("image_metadata", {})["image_base64"] = actions["image_base64"]

    # Apply independent edits (one must not affect the other)
    if state["hitl_actions"]["edited_twitter"]:
        state["approved_twitter_post"] = state["hitl_actions"]["edited_twitter"]
    if state["hitl_actions"]["edited_linkedin"]:
        state["approved_linkedin_post"] = state["hitl_actions"]["edited_linkedin"]

    # Treat explicit edits as an approval signal (human-in-the-loop approval),
    # while still honoring reject_content if set.
    if (
        (state["hitl_actions"]["edited_twitter"] or state["hitl_actions"]["edited_linkedin"])
        and not state["hitl_actions"]["approve_content"]
        and not state["hitl_actions"]["reject_content"]
    ):
        state["hitl_actions"]["approve_content"] = True

    # If no edits, approvals mirror drafts by default (on approve_content)
    if state.get("hitl_actions", {}).get("approve_content"):
        state.setdefault("approved_twitter_post", state.get("twitter_draft", ""))
        state.setdefault("approved_linkedin_post", state.get("linkedin_draft", ""))

    # Reject content always terminates (enforced at routing too)
    if state.get("hitl_actions", {}).get("reject_content"):
        state["terminated"] = True
        state["terminate_reason"] = "Human rejected content."

    state["updated_at"] = now_iso()
    return state


async def await_human_actions(state: AgentState) -> AgentState:
    """
    LangGraph interrupt: pause and send a payload to the Agent Inbox.
    The graph resumes when backend submits a HITL action payload.
    """
    if state.get("terminated"):
        return state

    payload = {
        "execution_id": state.get("execution_id"),
        "user_id": state.get("user_id"),
        "url": state.get("url"),
        "twitter_draft": state.get("twitter_draft", ""),
        "linkedin_draft": state.get("linkedin_draft", ""),
        "image_metadata": state.get("image_metadata", {}),
        "analysis_result": state.get("analysis_result", {}),
        "note": "Awaiting human actions (edit/approve/reject/regenerate).",
    }

    actions = interrupt(payload)  # <-- pauses execution; backend resumes with a dict
    if isinstance(actions, dict):
        state = apply_hitl_actions(state, actions)
    return state


def route_after_hitl(state: AgentState) -> Literal[
    "terminate",
    "await_more",
    "regen_twitter",
    "regen_linkedin",
    "continue_no_image",
    "continue_with_image",
]:
    a = state.get("hitl_actions") or {}
    if state.get("terminated") or a.get("reject_content"):
        return "terminate"
    if a.get("regenerate_twitter"):
        return "regen_twitter"
    if a.get("regenerate_linkedin"):
        return "regen_linkedin"

    # If no explicit decision, go back to inbox (do not auto-advance).
    if not a.get("approve_content"):
        return "await_more"

    # Reject image should not terminate; it just routes to no-image path.
    if a.get("reject_image"):
        return "continue_no_image"
    if a.get("approve_image") and (state.get("image_metadata") or {}).get("image_url"):
        return "continue_with_image"
    return "continue_no_image"

