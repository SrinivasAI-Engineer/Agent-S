from __future__ import annotations

from app.state import AgentState, now_iso


async def ingest_url(state: AgentState) -> AgentState:
    # Minimal validation; deeper checks happen post-scrape/analyze.
    state["updated_at"] = now_iso()
    if not state.get("url") or not state.get("user_id") or not state.get("execution_id"):
        state["terminated"] = True
        state["terminate_reason"] = "Missing required input: user_id/url/execution_id"
    return state

