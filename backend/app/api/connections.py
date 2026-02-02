"""List, add (via OAuth), update label/default, delete social connections."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.db import delete_connection, list_connections, update_connection
from app.logging import get_logger

router = APIRouter(prefix="/v1/connections", tags=["connections"])
log = get_logger(__name__)


class ConnectionUpdate(BaseModel):
    label: str | None = None
    is_default: bool | None = None


@router.get("")
async def list_connections_for_user(user_id: str = Depends(get_current_user_id)):
    """List all Twitter and LinkedIn connections for the current user."""
    return list_connections(user_id)


@router.delete("/{connection_id:int}")
async def remove_connection(connection_id: int, user_id: str = Depends(get_current_user_id)):
    """Remove a connected account."""
    ok = delete_connection(connection_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"ok": True}


@router.patch("/{connection_id:int}")
async def patch_connection(
    connection_id: int,
    body: ConnectionUpdate,
    user_id: str = Depends(get_current_user_id),
):
    """Update label and/or set as default."""
    ok = update_connection(
        connection_id,
        user_id,
        label=body.label,
        is_default=body.is_default,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"ok": True}
