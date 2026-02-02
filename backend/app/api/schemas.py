from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class CreateExecutionRequest(BaseModel):
    url: str = Field(min_length=8)


class ExecutionSummary(BaseModel):
    execution_id: str
    user_id: str
    url: str
    status: str
    updated_at: str
    interrupt: Optional[dict[str, Any]] = None


class ExecutionStateResponse(BaseModel):
    execution_id: str
    status: str
    state: dict[str, Any]


class HitlActionsRequest(BaseModel):
    approve_content: Optional[bool] = False
    reject_content: Optional[bool] = False
    approve_image: Optional[bool] = False
    reject_image: Optional[bool] = False
    regenerate_twitter: Optional[bool] = False
    regenerate_linkedin: Optional[bool] = False
    edited_twitter: Optional[str] = ""
    edited_linkedin: Optional[str] = ""
    twitter_connection_id: Optional[int] = None  # which Twitter account to post to
    linkedin_connection_id: Optional[int] = None  # which LinkedIn account to post to
    # Optional: base64 image bytes from frontend (browser can load when server returns 403)
    image_base64: Optional[str] = None

