from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, TypedDict


class ImageMetadata(TypedDict, total=False):
    image_url: str
    caption: str
    source: str  # e.g. "firecrawl"
    image_base64: str  # optional: from frontend or select_image when host allows


class MediaIds(TypedDict, total=False):
    twitter_media_id: str
    linkedin_asset_urn: str


class PublishStatus(TypedDict, total=False):
    twitter: Literal["not_started", "skipped", "published", "failed"]
    linkedin: Literal["not_started", "skipped", "published", "failed"]
    tweet_id: str
    linkedin_post_urn: str
    last_error: str


class HitlActions(TypedDict, total=False):
    # checkboxes
    approve_content: bool
    reject_content: bool
    approve_image: bool
    reject_image: bool
    regenerate_twitter: bool
    regenerate_linkedin: bool
    # edits
    edited_twitter: str
    edited_linkedin: str
    # metadata
    acted_at: str


class AnalysisResult(TypedDict, total=False):
    topic: str
    key_insights: list[str]
    tone: str
    relevance_score: float  # 0..1


class ScrapedContent(TypedDict, total=False):
    title: str
    url: str
    text: str
    headings: list[str]
    metadata: dict[str, Any]
    images: list[dict[str, Any]]  # includes src/alt/caption where available


class AuthTokens(TypedDict, total=False):
    # Stored in DB; state carries a *summary* only
    twitter_present: bool
    twitter_expires_at: Optional[str]
    linkedin_present: bool
    linkedin_expires_at: Optional[str]


class AgentState(TypedDict, total=False):
    # Mandatory schema from spec
    user_id: str
    url: str
    execution_id: str

    scraped_content: ScrapedContent
    analysis_result: AnalysisResult

    twitter_draft: str
    linkedin_draft: str

    approved_twitter_post: str
    approved_linkedin_post: str

    image_metadata: ImageMetadata
    media_ids: MediaIds
    auth_tokens: AuthTokens
    publish_status: PublishStatus
    hitl_actions: HitlActions

    # internal bookkeeping
    created_at: str
    updated_at: str
    terminated: bool
    terminate_reason: str


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

