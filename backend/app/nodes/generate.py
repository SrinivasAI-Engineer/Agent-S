from __future__ import annotations

from typing import Literal, Optional

from app.llm import SYSTEM_PROMPT, get_llm
from app.logging import get_logger
from app.state import AgentState, now_iso

logger = get_logger(__name__)


def _fallback_twitter(title: str, insights: list[str], url: str) -> str:
    base = title.strip() or "A useful read"
    bullets = " ".join([f"{i+1}) {x}" for i, x in enumerate(insights[:3]) if x])
    draft = f"{base}\n\n{bullets}\n\nRead: {url}\n\n#AI #Tech"
    return draft[:280].rstrip()


def _fallback_linkedin(title: str, insights: list[str], url: str) -> str:
    base = title.strip() or "Key takeaways"
    lines = [base, "", "Highlights:"]
    for x in insights[:5]:
        lines.append(f"- {x}")
    lines += ["", f"Source: {url}"]
    return "\n".join(lines).strip()


# ---- Twitter prompt (aligned structure) ----
TWITTER_PROMPT_TEMPLATE = (
    "Write a Twitter/X post (max 280 characters).\n\n"
    "ROLE:\n"
    "You are reacting to the article like a sharp, informed human—not summarizing it.\n\n"
    "STYLE & VOICE:\n"
    "- Opinionated, compressed, conversational\n"
    "- Sentence fragments are allowed\n"
    "- Avoid policy-report or academic tone\n\n"
    "STRUCTURE (MANDATORY):\n"
    "- Start with a sharp claim, contrast, or uncomfortable truth\n"
    "- Use at most 2 sentences (line breaks allowed)\n"
    "- End with a pointed implication or takeaway\n\n"
    "HARD CONSTRAINTS:\n"
    "- Pick ONE insight only\n"
    "- Do NOT explain background\n"
    "- Do NOT sound like a headline or abstract\n"
    "- Do NOT invent facts\n"
    "- Include the source URL at the end of the post\n\n"
    "HASHTAG RULES:\n"
    "- Use hashtags only if they add real discoverability value\n"
    "- Maximum 1 hashtag (2 only if extremely relevant)\n"
    "- Hashtags must be concrete (e.g., #Biodiversity, #AI), not generic\n"
    "- Place hashtags at the very end of the post\n\n"
    "LANGUAGE FILTER:\n"
    "- Rewrite any sentence that sounds like an abstract or policy report\n"
    "- Prefer contrast ('but', 'yet', 'instead') over explanation\n"
    "- If the post could be a paper summary, it fails\n\n"
    "URL VALIDATION:\n"
    "- Verify the URL format matches the provided source exactly\n"
    "- If the URL appears malformed or duplicated, replace it with the original\n\n"
    "TITLE:\n{title}\n\n"
    "KEY INSIGHTS (choose ONE):\n{insights}\n\n"
    "ARTICLE (source of truth):\n{text}"
)

# ---- LinkedIn prompt (aligned structure) ----
LINKEDIN_PROMPT_TEMPLATE = (
    "Write a LinkedIn post for a thoughtful professional audience "
    "(founders, operators, senior ICs). Length: 900–1300 characters.\n\n"
    "ROLE:\n"
    "You are sharing ONE idea from this article that changed how you think.\n\n"
    "STYLE & VOICE:\n"
    "- Plainspoken, reflective, confident\n"
    "- No academic or policy language\n"
    "- Write like a smart operator, not a researcher\n"
    "- Short paragraphs (1–2 sentences)\n\n"
    "STRUCTURE (MANDATORY):\n"
    "1. Opening tension or analogy (2 lines max)\n"
    "2. One core insight (why it matters)\n"
    "3. Practical implication for decision-makers\n"
    "4. Personal or reflective closing question (not generic)\n\n"
    "HARD CONSTRAINTS:\n"
    "- Focus on ONE idea only\n"
    "- Do NOT list programs, frameworks, or acronyms unless essential\n"
    "- Do NOT explain the entire article\n"
    "- Avoid phrases that sound like reports or essays\n"
    "- Do NOT invent facts\n"
    "- Include the source URL at the end\n\n"
    "HASHTAG RULES:\n"
    "- Include 4–6 relevant hashtags\n"
    "- Use professional, topic-specific hashtags\n"
    "- Avoid generic or viral hashtags\n"
    "- Place all hashtags at the very end of the post\n"
    "- Prefer CamelCase hashtags (e.g., #BiodiversityPolicy, #PredictiveAnalytics)\n\n"
    "SCOPE LIMIT:\n"
    "- Express only ONE core idea\n"
    "- If more than one insight appears, delete the weaker ones\n\n"
    "URL VALIDATION:\n"
    "- Verify the URL format matches the provided source exactly\n"
    "- If the URL appears malformed or duplicated, replace it with the original\n\n"
    "TITLE:\n{title}\n\n"
    "KEY INSIGHTS (select ONE primary idea):\n{insights}\n\n"
    "ARTICLE (source of truth):\n{text}"
)


async def generate_posts(
    state: AgentState,
    mode: Literal["both", "twitter_only", "linkedin_only"] = "both",
) -> AgentState:
    if state.get("terminated"):
        return state

    execution_id = state.get("execution_id", "unknown")
    logger.info(f"Starting post generation mode={mode}, execution_id={execution_id}")

    scraped = state.get("scraped_content") or {}
    analysis = state.get("analysis_result") or {}
    title = (scraped.get("title") or "").strip()
    url = state.get("url") or scraped.get("url") or ""
    text = (scraped.get("text") or "").strip()
    insights = [str(x) for x in (analysis.get("key_insights") or []) if str(x).strip()]

    llm = get_llm()
    if not llm:
        logger.warning(f"LLM not configured, using fallback generation for execution_id={execution_id}")

    async def gen_twitter() -> str:
        if not llm:
            return _fallback_twitter(title, insights, url)
        insights_blob = "\n".join(insights[:6]) if insights else "(none)"
        prompt = TWITTER_PROMPT_TEMPLATE.format(
            title=title,
            insights=insights_blob,
            text=text[:6000],
        )
        msg = await llm.ainvoke([SYSTEM_PROMPT, {"role": "user", "content": prompt}])
        s = msg.content if hasattr(msg, "content") else str(msg)
        return s.strip()[:280]

    async def gen_linkedin() -> str:
        if not llm:
            return _fallback_linkedin(title, insights, url)
        insights_blob = "\n".join(insights[:8]) if insights else "(none)"
        prompt = LINKEDIN_PROMPT_TEMPLATE.format(
            title=title,
            insights=insights_blob,
            text=text[:8000],
        )
        msg = await llm.ainvoke([SYSTEM_PROMPT, {"role": "user", "content": prompt}])
        s = msg.content if hasattr(msg, "content") else str(msg)
        return s.strip()

    if mode in ("both", "twitter_only"):
        logger.info(f"Generating Twitter post for execution_id={execution_id}")
        state["twitter_draft"] = await gen_twitter()
        logger.info(f"Twitter post generated for execution_id={execution_id}")
    if mode in ("both", "linkedin_only"):
        logger.info(f"Generating LinkedIn post for execution_id={execution_id}")
        state["linkedin_draft"] = await gen_linkedin()
        logger.info(f"LinkedIn post generated for execution_id={execution_id}")

    state["updated_at"] = now_iso()
    logger.info(f"Post generation completed mode={mode}, execution_id={execution_id}")
    return state
