from __future__ import annotations

from typing import Any, Literal, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.nodes.analyze import analyze_content
from app.nodes.auth import check_authentication
from app.nodes.generate import generate_posts
from app.nodes.hitl import await_human_actions, route_after_hitl
from app.nodes.image import select_image
from app.nodes.ingest import ingest_url
from app.nodes.scrape import scrape_content
from app.state import AgentState


async def generate_both(state: AgentState) -> AgentState:
    return await generate_posts(state, mode="both")


async def generate_twitter_only(state: AgentState) -> AgentState:
    return await generate_posts(state, mode="twitter_only")


async def generate_linkedin_only(state: AgentState) -> AgentState:
    return await generate_posts(state, mode="linkedin_only")


# Publishing nodes are wired in app/publish.py to keep graph modular
from app.publish import publish_linkedin, publish_twitter, upload_image  # noqa: E402


def build_graph(checkpointer=None):
    """Build the LangGraph. If checkpointer is provided (e.g. SqliteSaver), checkpoints persist across restarts."""
    builder = StateGraph(AgentState)

    builder.add_node("ingest_url", ingest_url)
    builder.add_node("scrape_content", scrape_content)
    builder.add_node("analyze_content", analyze_content)
    builder.add_node("generate_posts", generate_both)
    builder.add_node("select_image", select_image)
    builder.add_node("await_human_actions", await_human_actions)
    builder.add_node("generate_twitter_only", generate_twitter_only)
    builder.add_node("generate_linkedin_only", generate_linkedin_only)
    builder.add_node("check_authentication", check_authentication)
    builder.add_node("upload_image", upload_image)
    builder.add_node("publish_twitter", publish_twitter)
    builder.add_node("publish_linkedin", publish_linkedin)

    # START -> core pipeline
    builder.add_edge(START, "ingest_url")
    builder.add_edge("ingest_url", "scrape_content")
    builder.add_edge("scrape_content", "analyze_content")
    builder.add_edge("analyze_content", "generate_posts")
    builder.add_edge("generate_posts", "select_image")
    builder.add_edge("select_image", "await_human_actions")

    # HITL conditional routing (MANDATORY)
    builder.add_conditional_edges(
        "await_human_actions",
        route_after_hitl,
        {
            "terminate": END,
            "await_more": "await_human_actions",
            "regen_twitter": "generate_twitter_only",
            "regen_linkedin": "generate_linkedin_only",
            "continue_no_image": "check_authentication",
            "continue_with_image": "check_authentication",
        },
    )

    # After regeneration, go back to inbox (do not auto-publish)
    builder.add_edge("generate_twitter_only", "await_human_actions")
    builder.add_edge("generate_linkedin_only", "await_human_actions")

    # After auth, branch to upload if image approved, else publish text
    def _route_after_auth(state: AgentState) -> Literal["upload", "no_image", "terminate"]:
        if state.get("terminated"):
            return "terminate"
        a = state.get("hitl_actions") or {}
        if a.get("approve_image") and not a.get("reject_image") and (state.get("image_metadata") or {}).get("image_url"):
            return "upload"
        return "no_image"

    builder.add_conditional_edges(
        "check_authentication",
        _route_after_auth,
        {"terminate": END, "upload": "upload_image", "no_image": "publish_twitter"},
    )

    # Upload then publish
    builder.add_edge("upload_image", "publish_twitter")
    builder.add_edge("publish_twitter", "publish_linkedin")
    builder.add_edge("publish_linkedin", END)

    # Persistent checkpointer when provided (e.g. from main.py startup); else in-memory.
    # Execution state is also persisted to DB on interrupt and on submit_actions.
    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    memory = MemorySaver()
    return builder.compile(checkpointer=memory)


def get_interrupt_payload(result: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    LangGraph returns interrupts under '__interrupt__' as a list of Interrupt objects.
    We store only the first interrupt's 'value' (our JSON payload).
    """
    intr = result.get("__interrupt__")
    if not intr:
        return None
    try:
        first = intr[0]
        return getattr(first, "value", None) or None
    except Exception:
        return None

