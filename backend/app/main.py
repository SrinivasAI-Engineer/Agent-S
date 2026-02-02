from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth_routes import router as auth_router
from app.api.connections import router as connections_router
from app.api.executions import router as executions_router
from app.api.oauth import router as oauth_router
from app.config import settings
from app.db import init_db, mark_stuck_running_executions
from app.graph import build_graph
from app.logging import get_logger


def create_app() -> FastAPI:
    app = FastAPI(title="AgentSocialS Backend", version="0.1.0")

    # Allow all localhost origins in development, specific origin in production
    # In dev mode, be permissive to handle any localhost port
    if settings.app_env == "dev":
        # Use a regex pattern or allow all localhost origins
        # For simplicity, we'll allow common dev ports and the configured one
        allowed_origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            settings.frontend_base_url,
        ]
        # Remove duplicates while preserving order
        allowed_origins = list(dict.fromkeys(allowed_origins))
    else:
        allowed_origins = [settings.frontend_base_url]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def _startup() -> None:
        init_db()
        log = get_logger(__name__)
        n = mark_stuck_running_executions("Server restarted (e.g. --reload). Start a new execution.")
        if n:
            log.info("Marked %s stuck 'running' execution(s) as terminated (server restarted)", n)
        key = settings.gemini_api_key or ""
        masked = f"{key[:8]}...{key[-4:]}" if len(key) >= 12 else "(not set)"
        log.info("Gemini: model=%s, api_key=%s", settings.gemini_model, masked)
        # Persistent LangGraph checkpoints (async; requires aiosqlite)
        backend_dir = Path(__file__).resolve().parent.parent
        cp_path = str(backend_dir / "agentsocials.checkpoints.db")
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            cp_cm = AsyncSqliteSaver.from_conn_string(cp_path)
            app.state._checkpointer_cm = cp_cm
            app.state.checkpointer = await cp_cm.__aenter__()
            app.state.graph = build_graph(app.state.checkpointer)
            log.info("LangGraph using persistent checkpointer (AsyncSqliteSaver): %s", cp_path)
        except Exception as e:
            log.warning("Persistent checkpointer unavailable (%s), using in-memory", e)
            app.state._checkpointer_cm = None
            app.state.checkpointer = None
            app.state.graph = build_graph(None)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        cm = getattr(app.state, "_checkpointer_cm", None)
        if cm is not None:
            await cm.__aexit__(None, None, None)
            app.state._checkpointer_cm = None

    app.include_router(auth_router)
    app.include_router(connections_router)
    app.include_router(executions_router)
    app.include_router(oauth_router)
    return app


app = create_app()

