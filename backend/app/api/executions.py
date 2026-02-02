from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from langgraph.types import Command

from app.api.schemas import CreateExecutionRequest, ExecutionStateResponse, ExecutionSummary, HitlActionsRequest
from app.auth import get_current_user_id
from app.db import (
    compute_idempotency_key,
    create_execution,
    find_execution_by_idempotency,
    get_execution,
    list_inbox,
    save_execution_state,
)
from app.graph import get_interrupt_payload
from app.logging import get_logger
from app.publish import _download_bytes
from app.state import now_iso

router = APIRouter(prefix="/v1", tags=["executions"])
logger = get_logger(__name__)


def get_graph(request: Request):
    """Graph is built at startup with persistent checkpointer (see main.py)."""
    return request.app.state.graph

# Max time for one full graph run (scrape + analyze + generate + select_image + interrupt)
GRAPH_TIMEOUT_SECONDS = 300


def _status_from_interrupt(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "running"
    if payload.get("type") == "reauth_required":
        return "awaiting_auth"
    return "awaiting_human"


def _make_serializable_state(result: dict[str, Any]) -> dict[str, Any]:
    """Copy state and replace __interrupt__ with JSON-serializable payload."""
    out = result.copy()
    if "__interrupt__" in out:
        payload = get_interrupt_payload(result)
        if payload:
            out["__interrupt__"] = [{"value": payload}]
        else:
            out.pop("__interrupt__", None)
    return out


@router.post("/executions", response_model=ExecutionStateResponse)
async def create_execution_endpoint(req: CreateExecutionRequest, user_id: str = Depends(get_current_user_id), graph=Depends(get_graph)):
    idem = compute_idempotency_key(user_id, req.url)
    existing = find_execution_by_idempotency(user_id, idem)
    if existing and existing.status in {"running", "awaiting_human", "awaiting_auth"}:
        state = json.loads(existing.state_json or "{}")
        return ExecutionStateResponse(execution_id=existing.execution_id, status=existing.status, state=state)

    execution_id = uuid.uuid4().hex
    initial_state: dict[str, Any] = {
        "user_id": user_id,
        "url": req.url,
        "execution_id": execution_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "terminated": False,
        "terminate_reason": "",
        "publish_status": {"twitter": "not_started", "linkedin": "not_started"},
    }

    create_execution(execution_id, user_id, req.url, initial_state, idem)

    async def run_execution(g) -> None:
        logger.info("Execution %s started (url=%s)", execution_id, req.url)
        # Note: if you use uvicorn --reload, saving a file kills this task and the execution stays "running" until next server start
        try:
            result = await asyncio.wait_for(
                g.ainvoke(initial_state, config={"configurable": {"thread_id": execution_id}}),
                timeout=float(GRAPH_TIMEOUT_SECONDS),
            )
            serializable = _make_serializable_state(result)
            payload = get_interrupt_payload(result)
            status = _status_from_interrupt(payload)
            if result.get("terminated"):
                status = "terminated"
            elif not payload:
                status = "completed"
            save_execution_state(execution_id, serializable, status=status)
            logger.info("Execution %s finished with status=%s", execution_id, status)
        except asyncio.TimeoutError:
            logger.error("Execution %s timed out after %s seconds", execution_id, GRAPH_TIMEOUT_SECONDS)
            err_state = {**initial_state, "terminated": True, "terminate_reason": "Execution timed out (5 min).", "updated_at": now_iso()}
            save_execution_state(execution_id, err_state, status="terminated")
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str:
                logger.warning("Execution %s: Gemini quota exceeded", execution_id)
                err_state = {**initial_state, "terminated": True, "terminate_reason": "Gemini API quota exceeded. Set GEMINI_MODEL=gemini-pro in .env or try later.", "updated_at": now_iso()}
                save_execution_state(execution_id, err_state, status="terminated")
            else:
                logger.exception("Execution %s failed", execution_id)
                err_state = {**initial_state, "terminated": True, "terminate_reason": str(e), "updated_at": now_iso()}
                save_execution_state(execution_id, err_state, status="terminated")

    asyncio.create_task(run_execution(graph))
    return ExecutionStateResponse(execution_id=execution_id, status="running", state=initial_state)


def _image_origin_referer(image_url: str) -> str:
    """Return origin (scheme + netloc) of image URL for use as Referer when article referer fails."""
    try:
        p = urlparse(image_url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}/"
    except Exception:
        pass
    return ""


@router.get("/proxy-image")
async def proxy_image(
    url: str = Query(..., min_length=10),
    referer: str | None = Query(None),
    user_id: str = Depends(get_current_user_id),
):
    """Download image with optional Referer and return base64. Used when frontend fetch is blocked by CORS."""
    url_decoded = unquote(unquote(url))  # handle double-encoding from query
    referer_decoded = unquote(referer) if referer else None
    blob = None
    last_err: str | None = None
    # Try article referer first, then image origin (CDNs often allow same-origin referer)
    for ref in (referer_decoded, _image_origin_referer(url_decoded)):
        try:
            blob = await _download_bytes(url_decoded, referer=ref or None)
            break
        except Exception as e:
            last_err = str(e)
            if "403" in last_err or "Forbidden" in last_err:
                continue
            raise HTTPException(status_code=502, detail=last_err[:200])
    if blob is None:
        raise HTTPException(status_code=502, detail=last_err[:200] if last_err else "Image download failed")
    if len(blob) > 10 * 1024 * 1024:  # 10 MB max
        raise HTTPException(status_code=400, detail="Image too large")
    return {"base64": base64.b64encode(blob).decode("ascii")}


@router.get("/inbox", response_model=list[ExecutionSummary])
async def inbox(user_id: str = Depends(get_current_user_id)):
    rows = list_inbox(["awaiting_human", "awaiting_auth"], user_id=user_id)
    out: list[ExecutionSummary] = []
    for r in rows:
        state = json.loads(r.state_json or "{}")
        payload = get_interrupt_payload(state) or None
        out.append(
            ExecutionSummary(
                execution_id=r.execution_id,
                user_id=r.user_id,
                url=r.url,
                status=r.status,
                updated_at=r.updated_at.replace(microsecond=0).isoformat() + "Z",
                interrupt=payload,
            )
        )
    return out


@router.get("/executions/{execution_id}", response_model=ExecutionStateResponse)
async def get_execution_state(execution_id: str, user_id: str = Depends(get_current_user_id)):
    try:
        ex = get_execution(execution_id)
        if ex.user_id != user_id:
            raise HTTPException(status_code=404, detail="Execution not found")
        state = json.loads(ex.state_json or "{}")
        return ExecutionStateResponse(execution_id=execution_id, status=ex.status, state=state)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/executions/{execution_id}/actions", response_model=ExecutionStateResponse)
async def submit_actions(execution_id: str, req: HitlActionsRequest, user_id: str = Depends(get_current_user_id), graph=Depends(get_graph)):
    ex = get_execution(execution_id)
    if ex.user_id != user_id:
        raise HTTPException(status_code=404, detail="Execution not found")

    config = {"configurable": {"thread_id": execution_id}}
    # Restore checkpoint from DB when checkpointer lost it (e.g. after server restart).
    # This ensures resume has user_id/url/execution_id and full state instead of empty.
    try:
        snapshot = await graph.aget_state(config)
        if not snapshot.values or not snapshot.values.get("execution_id"):
            loaded = json.loads(ex.state_json or "{}")
            loaded.pop("__interrupt__", None)
            if loaded.get("user_id") and loaded.get("url") and loaded.get("execution_id"):
                await graph.aupdate_state(config, loaded, as_node="select_image")
    except Exception:
        loaded = json.loads(ex.state_json or "{}")
        loaded.pop("__interrupt__", None)
        if loaded.get("user_id") and loaded.get("url") and loaded.get("execution_id"):
            await graph.aupdate_state(config, loaded, as_node="select_image")

    result = await graph.ainvoke(Command(resume=req.model_dump()), config=config)
    serializable = _make_serializable_state(result)
    payload = get_interrupt_payload(result)
    status = _status_from_interrupt(payload)
    if result.get("terminated"):
        status = "terminated"
    elif not payload:
        status = "completed"

    save_execution_state(execution_id, serializable, status=status)
    return ExecutionStateResponse(execution_id=execution_id, status=status, state=serializable)

