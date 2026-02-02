# Migration: API-Based Publishing → MCP-Based Modular Publishing

This document describes the refactor from **direct platform API calls** in LangGraph to **MCP-based modular publishing**, plus **persistent Agent Inbox** and **resumable executions**.

---

## One-line intent

**Refactor publishing from direct APIs to the MCP layer while keeping LangGraph logic intact, and fix persistence and resumability.**

---

## What changed (high level)

| Before | After |
|--------|--------|
| LangGraph `publish_twitter` / `publish_linkedin` called Twitter/LinkedIn APIs directly and read tokens from DB | LangGraph publish nodes **only** call the **MCP publishing layer**; no platform APIs, no token access |
| OAuth and token storage in backend; publish nodes used `get_connection_tokens`, `update_connection_tokens` | OAuth and token storage stay in backend; **only the MCP layer** reads/refreshes tokens and calls platform APIs |
| In-memory LangGraph checkpointer (MemorySaver) | **Persistent** checkpointer (SqliteSaver) when available; execution state also persisted to DB on interrupt and on submit |
| Inbox and resume relied on DB state + manual restore on submit | Inbox and resume use **DB state** + **persistent checkpoints**; reload and server restart keep inbox and allow resume |

---

## 1. MCP-based publishing

### 1.1 Design

- **LangGraph** keeps the same nodes and HITL flow: `upload_image`, `publish_twitter`, `publish_linkedin`.
- **Publish nodes** no longer call platform APIs or touch credentials. They only:
  - Read from **state** (user_id, connection_id, text, media_ids).
  - Call **MCP client**: `call_publish_post(platform, text, user_id, connection_id, media_id, metadata)` and `call_upload_media(platform, media_base64, user_id, connection_id)`.
  - Update **state** from the result (post_id, status, error).

- **MCP layer** (used in-process by default):
  - **`mcp_publish/client.py`**: `call_publish_post`, `call_upload_media` — used by LangGraph.
  - **`mcp_publish/server.py`**: Same logic; reads tokens from DB, refreshes if needed, calls Twitter/LinkedIn APIs. Can also be run as a separate MCP server (Streamable HTTP) if desired.

- **OAuth** remains in the FastAPI backend (start/callback); tokens are stored in the shared DB. Only the MCP layer reads/updates tokens when publishing.

### 1.2 Constraints satisfied

- LangGraph does **not** call platform APIs.
- LangGraph does **not** store or read OAuth tokens.
- HITL flow is **unchanged** (same nodes, same interrupts, same resume points).
- MCP handles execution (API calls, retries, token refresh); LangGraph handles state, HITL, and routing.

---

## 2. Persistence and resumability

### 2.1 Execution state in DB

- **Execution** table stores `state_json` and `status`.
- State is saved:
  - When the run **interrupts** (awaiting_human / awaiting_auth).
  - When the user **submits actions** (after resume).
  - On **timeout** or **error** (terminated).

- **Inbox** is `list_inbox(["awaiting_human", "awaiting_auth"], user_id)` — always from DB. So after a page reload or server restart, the inbox is still populated from the DB.

### 2.2 LangGraph checkpoints

- On **startup**, the app tries to use **SqliteSaver** with `agentsocials.checkpoints.db` in the backend directory.
- If that succeeds, the **graph** is built with this checkpointer so checkpoints **persist across restarts**.
- If it fails (e.g. missing dependency), the app falls back to **MemorySaver** (in-memory only).

- **Resume**: On `POST /executions/{id}/actions`, the code restores state from DB into the graph when the checkpointer has no checkpoint for that thread (e.g. after a restart with MemorySaver). So resume works even without persistent checkpoints; with SqliteSaver, the graph also has full checkpoint history.

### 2.3 Summary

- **Persistent Agent Inbox**: Inbox is read from DB; no data loss on reload.
- **Resume paused agents**: Submit actions restores state from DB when needed and resumes the graph; with SqliteSaver, checkpoints also persist across restarts.

---

## 3. User authentication (unchanged)

- **Signup / login / JWT** and **session management** are unchanged.
- **OAuth** for Twitter/LinkedIn remains in the backend; tokens are stored in the shared DB and used **only by the MCP layer** when publishing.
- Users see only their own executions and inbox (filtered by `user_id`).

---

## 4. File-level changes

| Area | Changes |
|------|--------|
| **`app/publish.py`** | All direct API and token usage removed. Uses `mcp_publish.client.call_publish_post` and `call_upload_media` only. Keeps state handling, `_image_is_from_scrape`, `_download_bytes`, and user-facing error messages. |
| **`mcp_publish/client.py`** | New. Exposes `call_publish_post` and `call_upload_media` (in-process call into MCP logic). |
| **`mcp_publish/server.py`** | Existing. Implements publish/upload and token refresh; used by client and by the optional standalone MCP server. |
| **`app/graph.py`** | `build_graph(checkpointer=None)`. Uses provided checkpointer (e.g. SqliteSaver) or falls back to MemorySaver. |
| **`app/main.py`** | Startup: creates SqliteSaver (when possible), enters context, builds graph with it, stores graph (and optional CM) on `app.state`. |
| **`app/api/executions.py`** | Graph is taken from `request.app.state.graph` via `get_graph`. Create and submit endpoints use this graph. Resume still restores from DB when checkpoint is missing. |

---

## 5. How to run

- **Backend**: Unchanged. `uvicorn app.main:app --reload --port 8000`. Uses same `.env` and DB.
- **Optional MCP server process**: From backend dir, `python -m mcp_publish.server` (e.g. port 8001). Default flow uses **in-process** MCP client, so this is optional.
- **Persistence**: Ensure `langgraph-checkpoint-sqlite` is installed so SqliteSaver is used; otherwise the app uses in-memory checkpoints and still persists state to DB for inbox and resume.

---

## 6. READMEs

- **MCP server (creation, tools, run):** [`backend/mcp_publish/README.md`](../mcp_publish/README.md)
- **Backend (setup, MCP, persistence):** [`backend/README.md`](../README.md)
- **Root:** [`README.md`](../../README.md) — links to backend and MCP docs.

---

## 7. Testing checklist

- [ ] Create execution → reaches awaiting_human; reload page → inbox still shows it.
- [ ] Submit approve + post → Twitter/LinkedIn publish via MCP (no direct API in LangGraph).
- [ ] Restart backend → inbox still from DB; resume an awaiting_human execution → completes or moves forward as expected.
- [ ] Auth: signup/login and OAuth flows unchanged; only MCP layer uses tokens for publishing.
