from __future__ import annotations

import re

from app.llm import SYSTEM_PROMPT, get_llm
from app.logging import get_logger
from app.state import AgentState, now_iso

logger = get_logger(__name__)


def _simple_relevance(text: str) -> float:
    # Minimal heuristic; in production replace with classifier/LLM.
    if len(text) < 600:
        return 0.0
    bad_signals = ["cookie", "sign up", "pricing", "terms of service", "login"]
    penalty = sum(1 for s in bad_signals if s in text.lower()) * 0.05
    return max(0.0, min(1.0, 0.65 - penalty + min(0.35, len(text) / 10000)))


async def analyze_content(state: AgentState) -> AgentState:
    if state.get("terminated"):
        return state

    execution_id = state.get("execution_id", "unknown")
    logger.info(f"Starting content analysis for execution_id={execution_id}")

    scraped = state.get("scraped_content") or {}
    text = (scraped.get("text") or "").strip()
    title = (scraped.get("title") or "").strip()

    relevance = _simple_relevance(text)
    topic = title or "Article"
    tone = "informative"

    llm = get_llm()
    if llm:
        logger.info(f"Using LLM for analysis, execution_id={execution_id}")
    else:
        logger.info(f"Using simple heuristic for analysis, execution_id={execution_id}")
    if llm:
        prompt = (
            "Analyze this article and return JSON with keys: "
            "topic (string), key_insights (array of 3-6 strings), tone (string), relevance_score (0..1).\n\n"
            f"TITLE: {title}\n\n"
            f"CONTENT:\n{text[:9000]}"
        )
        msg = await llm.ainvoke([SYSTEM_PROMPT, {"role": "user", "content": prompt}])
        raw = msg.content if hasattr(msg, "content") else str(msg)
        # Soft JSON extraction
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                import json

                parsed = json.loads(m.group(0))
                topic = str(parsed.get("topic") or topic)
                key_insights = parsed.get("key_insights") or []
                tone = str(parsed.get("tone") or tone)
                relevance = float(parsed.get("relevance_score") or relevance)
                state["analysis_result"] = {
                    "topic": topic,
                    "key_insights": [str(x) for x in key_insights][:8],
                    "tone": tone,
                    "relevance_score": max(0.0, min(1.0, relevance)),
                }
            except Exception:
                state["analysis_result"] = {
                    "topic": topic,
                    "key_insights": [],
                    "tone": tone,
                    "relevance_score": relevance,
                }
        else:
            state["analysis_result"] = {
                "topic": topic,
                "key_insights": [],
                "tone": tone,
                "relevance_score": relevance,
            }
    else:
        state["analysis_result"] = {
            "topic": topic,
            "key_insights": [],
            "tone": tone,
            "relevance_score": relevance,
        }

    # Guard: low relevance terminates by default (can be changed to interrupt in future)
    relevance_score = state["analysis_result"].get("relevance_score") or 0.0
    if relevance_score < 0.35:
        logger.warning(f"Content relevance too low ({relevance_score:.2f}) for execution_id={execution_id}, terminating")
        state["terminated"] = True
        state["terminate_reason"] = f"Content relevance too low ({relevance_score:.2f}); not a suitable article/blog."

    state["updated_at"] = now_iso()
    logger.info(f"Content analysis completed for execution_id={execution_id}, relevance={state['analysis_result'].get('relevance_score', 0)}")
    return state

