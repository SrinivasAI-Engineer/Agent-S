# AgentSocialS — Project Insights & Technical Reference

This document is the **single source of truth** for the entire AgentSocialS project: workflow, technical terms, file roles, inter-component communication, MCP server flow, and usage. It lives in the `insights/` folder for easy discovery.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Technical Terms & Concepts](#2-technical-terms--concepts)
3. [End-to-End Workflow](#3-end-to-end-workflow)
4. [Repository Structure & File Roles](#4-repository-structure--file-roles)
5. [How Components Communicate](#5-how-components-communicate)
6. [MCP Server: Flow, Usage & Role](#6-mcp-server-flow-usage--role)
7. [Data Flow & Persistence](#7-data-flow--persistence)
8. [Authentication & Security](#8-authentication--security)
9. [Quick Reference: APIs & Endpoints](#9-quick-reference-apis--endpoints)

---

## 1. Project Overview

**AgentSocialS** is a **Human-in-the-Loop (HITL)** agent that:

- Accepts an **article/blog URL** from a logged-in user.
- **Scrapes** the URL (FireCrawl), **analyzes** content (LLM/heuristic), **generates** platform-specific drafts (Twitter, LinkedIn).
- **Selects** an image strictly from the article’s scraped assets.
- **Pauses** at an **interrupt** and sends a payload to the **Agent Inbox** (frontend).
- Resumes only when the user submits **HITL actions** (approve/reject, edit, regenerate, choose connection).
- **Publishes** to Twitter and LinkedIn only after explicit approval, delegating all platform API calls to an **MCP (Model Context Protocol) Publishing Server**.

**Guarantees:**

- No auto-posting: publishing runs only after HITL approval.
- Images come only from scraped article assets.
- Image upload happens only after HITL approval.
- OAuth and token storage live in the backend; the graph interrupts if tokens are missing/expired.

---

## 2. Technical Terms & Concepts

| Term | Meaning |
|------|--------|
| **LangGraph** | State-machine framework for multi-step workflows. Nodes run in sequence/conditionally; state is a shared TypedDict; **interrupts** pause execution until resumed with a payload. |
| **Interrupt** | A graph node calls `interrupt(payload)`, which pauses the run and returns control to the driver. The driver (e.g. FastAPI) persists state and sends the payload to the UI. When the user acts, the driver resumes with `Command(resume=actions)`. |
| **Checkpointer** | LangGraph component that saves/restores graph state per `thread_id`. This project uses **AsyncSqliteSaver** (persistent) or **MemorySaver** (in-memory) so runs can survive restarts. |
| **HITL (Human-in-the-Loop)** | The human reviews drafts in the Agent Inbox and submits actions: approve_content, reject_content, approve_image, reject_image, regenerate_twitter/linkedin, edited_twitter/linkedin, twitter_connection_id, linkedin_connection_id. |
| **Agent State (`AgentState`)** | TypedDict holding all data for one execution: user_id, url, execution_id, scraped_content, analysis_result, twitter_draft, linkedin_draft, image_metadata, hitl_actions, publish_status, connection IDs, etc. |
| **Execution** | One run of the graph for a single URL. Identified by `execution_id` (UUID hex). Stored in DB with status: `running`, `awaiting_human`, `awaiting_auth`, `completed`, `terminated`. |
| **MCP (Model Context Protocol)** | Protocol for tools/servers that agents can call. Here, the **MCP Publishing Server** exposes `publish_post` and `upload_media`; the backend/LangGraph call these instead of calling Twitter/LinkedIn directly. |
| **Connection** | A linked social account (Twitter or LinkedIn) for a user. Stored in `social_connections` with encrypted tokens; user can have multiple connections per platform and choose which to use at publish time. |
| **Idempotency key** | Hash of (user_id, url) used to avoid duplicate executions for the same URL while one is still running/awaiting_human/awaiting_auth. |

---

## 3. End-to-End Workflow

```
User submits URL (frontend)
    → POST /v1/executions { url }
    → Backend creates execution_id, initial state, DB row
    → Backend starts async graph run: ainvoke(initial_state, thread_id=execution_id)

Graph run:
    1. ingest_url     → Validate user_id, url, execution_id; set terminated if missing.
    2. scrape_content → FireCrawl API → markdown, html, metadata, images; terminate if content too short.
    3. analyze_content→ LLM (Gemini) or heuristic → topic, key_insights, tone, relevance_score; terminate if relevance < 0.35.
    4. generate_posts → LLM or fallback → twitter_draft, linkedin_draft.
    5. select_image   → Pick image from scraped images (prefer og:image); optionally fetch bytes (base64).
    6. await_human_actions → interrupt(payload) → PAUSE. Payload has drafts, image_metadata, etc.

Backend:
    → Saves state + status (e.g. awaiting_human) to DB.
    → Returns execution state to client. Frontend shows “Agent Inbox” with this execution.

User in frontend:
    → Sees drafts, image, checkboxes (approve/reject, regenerate, connection dropdowns).
    → Submits actions: POST /v1/executions/{id}/actions with HitlActionsRequest.

Backend:
    → Optionally restores state from DB into graph if checkpointer lost it (e.g. after restart).
    → graph.ainvoke(Command(resume=actions), thread_id=execution_id) → interrupt returns actions.
    → await_human_actions applies actions to state (approve_content, connection IDs, etc.).
    → route_after_hitl: terminate | await_more | regen_twitter | regen_linkedin | continue_no_image | continue_with_image.

If continue:
    7. check_authentication → Check default Twitter/LinkedIn tokens; if missing/expired → interrupt(reauth_required).
    8. upload_image (if continue_with_image) → MCP upload_media for Twitter & LinkedIn → media_ids.
    9. publish_twitter → MCP publish_post(twitter, text, user_id, connection_id, media_id).
    10. publish_linkedin → MCP publish_post(linkedin, text, user_id, connection_id, metadata with asset URN).
    11. END → state saved, status=completed.
```

---

## 4. Repository Structure & File Roles

### Root

| Path | Role |
|------|------|
| `README.md` | High-level project description, quickstart, guarantees, links to backend/frontend/MCP docs. |

### Backend (`backend/`)

| Path | Role |
|------|------|
| `app/main.py` | FastAPI app factory. CORS, startup: init_db, mark stuck executions, create AsyncSqliteSaver checkpointer, build_graph(checkpointer). Shutdown: close checkpointer. Mounts auth, connections, executions, oauth routers. |
| `app/config.py` | Pydantic Settings: app_env, database_url, tokens_fernet_key, jwt_*, firecrawl_*, gemini_*, twitter_*, linkedin_*. Reads backend `.env`; backend/.env overrides env vars. |
| `app/state.py` | TypedDicts: AgentState, ImageMetadata, MediaIds, PublishStatus, HitlActions, AnalysisResult, ScrapedContent, AuthTokens. Defines the schema of graph state and what LangGraph merges. |
| `app/graph.py` | Builds LangGraph StateGraph(AgentState). Adds nodes (ingest_url → scrape → analyze → generate → select_image → await_human_actions; check_authentication; upload_image; publish_twitter; publish_linkedin). Conditional edges: route_after_hitl, route_after_auth. Compiles with checkpointer or MemorySaver. get_interrupt_payload(result) for API layer. |
| `app/db.py` | SQLAlchemy models: User, Execution, TokenStore, SocialConnection, OAuthState. Engine from config. init_db, get_session, create_execution, save_execution_state, list_inbox, get_execution, find_execution_by_idempotency, mark_stuck_running_executions; create_user, get_user_by_email, get_user_by_id; OAuth state helpers; add_connection, list_connections, get_connection, get_connection_tokens, get_default_connection_id, get_default_connection_tokens, get_default_connection_expiry, update_connection_tokens, delete_connection, update_connection. |
| `app/auth.py` | Password hashing (bcrypt), JWT create/decode, get_current_user_id (Bearer), get_current_user_id_optional. Used by protected routes. |
| `app/security.py` | Fernet encrypt/decrypt for token storage (TOKENS_FERNET_KEY). Used by db when reading/writing SocialConnection.encrypted_json. |
| `app/llm.py` | get_llm() → ChatGoogleGenerativeAI (Gemini) if gemini_api_key set; else None. SYSTEM_PROMPT for social copywriter. Used by analyze and generate nodes. |
| `app/logging.py` | get_logger(__name__). Standard logging for backend. |
| `app/publish.py` | **LangGraph publishing nodes only.** No direct platform API calls. upload_image: validate image from scrape, get base64, call MCP client upload_media for Twitter & LinkedIn, set media_ids. publish_twitter / publish_linkedin: read connection_id from state, call MCP client publish_post. _download_bytes (httpx + Referer retry) for image fetch; used by select_image and proxy-image API. |
| `app/clients/firecrawl.py` | scrape_article(url) → FireCrawl v2 scrape API; returns dict with markdown, html, metadata, images. Raises FireCrawlError. Used by scrape_content node. |
| `app/nodes/ingest.py` | Validates state (user_id, url, execution_id); sets terminated if missing. |
| `app/nodes/scrape.py` | Calls firecrawl scrape_article; parses markdown/images/headings; enforces min content length; writes scraped_content. |
| `app/nodes/analyze.py` | Uses LLM or heuristic; produces analysis_result (topic, key_insights, tone, relevance_score); terminates if relevance < 0.35. |
| `app/nodes/generate.py` | Uses LLM or fallback templates; produces twitter_draft, linkedin_draft. Regeneration modes: twitter_only, linkedin_only. |
| `app/nodes/image.py` | select_image: from scraped_content.images choose one (prefer og:image, same-site); optionally _download_bytes → image_base64; set image_metadata. |
| `app/nodes/hitl.py` | await_human_actions: builds interrupt payload, calls interrupt(payload). On resume, apply_hitl_actions (set hitl_actions, connection IDs, approved posts, terminated). route_after_hitl returns next step. |
| `app/nodes/auth.py` | check_authentication: ensure default Twitter & LinkedIn tokens exist and not expired; else interrupt(reauth_required); on resume re-check and possibly terminate. |
| `app/api/schemas.py` | Pydantic: CreateExecutionRequest, ExecutionSummary, ExecutionStateResponse, HitlActionsRequest (all action fields + connection IDs + image_base64). |
| `app/api/executions.py` | POST /v1/executions (create, run graph in background, return execution_id/status/state). GET /v1/executions/{id}. GET /v1/inbox. POST /v1/executions/{id}/actions (restore state if needed, Command(resume=req.model_dump()), save state, return new state). GET /v1/proxy-image (backend image fetch for CORS). |
| `app/api/auth_routes.py` | POST /v1/auth/signup, POST /v1/auth/login, GET /v1/auth/me. Uses auth hash_password, create_access_token, get_current_user_id. |
| `app/api/connections.py` | GET /v1/connections, DELETE /v1/connections/{id}, PATCH /v1/connections/{id} (label, is_default). |
| `app/api/oauth.py` | GET /v1/oauth/twitter/start, /twitter/callback; GET /v1/oauth/linkedin/start, /linkedin/callback. PKCE/OAuth2; stores tokens in SocialConnection (encrypted). |
| `requirements.txt` | Python dependencies: FastAPI, uvicorn, pydantic, SQLAlchemy, aiosqlite, httpx, cryptography, bcrypt, PyJWT, langgraph, langgraph-checkpoint-sqlite, langchain-*, tenacity, etc. |

### MCP Publishing (`backend/mcp_publish/`)

| Path | Role |
|------|------|
| `server.py` | Defines **MCP tools** (e.g. via FastMCP or equivalent): `publish_post(platform, text, user_id, connection_id?, media_id?, metadata?)`, `upload_media(platform, media_base64, user_id, connection_id?, image_url?)`. Uses **same DB and config** as backend (app.config, app.db). Loads tokens from SocialConnection (get_connection_tokens, get_default_connection_id); calls Twitter/LinkedIn APIs (httpx); handles token refresh (Twitter), LinkedIn person_urn fetch. Can be run as a separate process: `python -m mcp_publish.server` (e.g. port 8001). |
| `client.py` | **In-process** MCP client used by LangGraph. Lazy-loads server’s _get_tools_impl() to get publish_post and upload_media functions. call_publish_post(...), call_upload_media(...) invoke those. So the backend does **not** call Twitter/LinkedIn directly; it calls these helpers, which in turn run the same logic as the MCP server (shared code path when run in-process). |
| `README.md` | MCP server creation, tools, run, env, how backend/LangGraph use it. |

**Note:** In the current setup, the **backend uses `mcp_publish.client`**, which imports `mcp_publish.server._get_tools_impl` and calls the tool functions **in-process**. So publishing does not require a separate MCP server process unless you choose to run the server over the network (e.g. Streamable HTTP). The README describes both the modular MCP design and how to run the server as a separate process.

### Frontend (`frontend/`)

| Path | Role |
|------|------|
| `src/main.tsx` | React root; mounts App; imports styles.css. |
| `src/ui/App.tsx` | **Single-page app.** AuthView: signup/login, store token in localStorage. MainApp: header (logo, user, logout), sidebar (New URL, Connections, Agent Inbox list), main (Execution Details, drafts, image, actions, submit). Calls API_BASE (/v1/...) with Bearer token. Creates execution (POST /v1/executions), loads inbox (GET /v1/inbox), loads execution (GET /v1/executions/{id}), submits actions (POST /v1/executions/{id}/actions), connections CRUD, OAuth links. |
| `src/ui/styles.css` | Global and component styles (Helvetica, corporate colors, header, sidebar, cards, list, buttons). |
| `src/public/ideyalabs-logo.png` | Logo asset. |
| `index.html` | HTML shell; root div; script src main.tsx. |
| `vite.config.ts` | Vite config; API proxy optional. |
| `package.json` | React, TypeScript, Vite deps. |

### Insights (`insights/`)

| Path | Role |
|------|------|
| `README.md` | This file: full project insights, workflow, file roles, communication, MCP flow. |

### Other

| Path | Role |
|------|------|
| `backend/.env.example` | Template for DATABASE_URL, TOKENS_FERNET_KEY, FIRECRAWL_API_KEY, GEMINI_*, TWITTER_*, LINKEDIN_*, JWT_*. |
| `backend/docs/MIGRATION_MCP_AND_PERSISTENCE.md` | Migration narrative: MCP architecture, persistence, file-level changes. |
| `frontend/.env.example` | Optional VITE_API_BASE for API URL. |

---

## 5. How Components Communicate

- **Frontend ↔ Backend**  
  - All API calls go to `VITE_API_BASE` (default `http://localhost:8000`) with `Authorization: Bearer <token>` (except signup/login).  
  - Auth: POST signup/login → token stored in localStorage; GET /v1/auth/me to get user.  
  - Executions: POST /v1/executions (body: url) → backend creates execution and starts graph; GET /v1/inbox and GET /v1/executions/{id} to read state; POST /v1/executions/{id}/actions to submit HITL and resume.  
  - Connections: GET/PATCH/DELETE /v1/connections; OAuth: open /v1/oauth/{twitter|linkedin}/start?user_id=... then callback stores connection.

- **Backend API ↔ LangGraph**  
  - Executions API holds `request.app.state.graph` (built at startup with checkpointer).  
  - Create: `graph.ainvoke(initial_state, config={"configurable": {"thread_id": execution_id}})`.  
  - Submit actions: optionally `graph.aupdate_state(config, loaded_state, as_node="select_image")` if checkpoint missing; then `graph.ainvoke(Command(resume=req.model_dump()), config)`.

- **LangGraph ↔ MCP (publishing)**  
  - Publish nodes (`upload_image`, `publish_twitter`, `publish_linkedin`) do **not** call Twitter/LinkedIn. They call `mcp_publish.client.call_upload_media` and `call_publish_post`.  
  - Client resolves server’s tool implementations (in-process by default). Those tools read DB (SocialConnection tokens), call platform APIs, handle refresh/LinkedIn URN.

- **Backend ↔ DB**  
  - SQLAlchemy + same `database_url` for FastAPI and (when used) MCP server. Tables: users, executions, tokens (legacy), social_connections, oauth_states.  
  - Execution state is also persisted in DB (`state_json`, status) on interrupt and on submit_actions; checkpointer (SQLite) persists graph checkpoints separately.

- **Backend ↔ FireCrawl / Gemini**  
  - FireCrawl: app.clients.firecrawl (httpx) → FireCrawl API.  
  - Gemini: app.llm.get_llm() → LangChain ChatGoogleGenerativeAI; used in analyze and generate nodes.

---

## 6. MCP Server: Flow, Usage & Role

### What “MCP” Means Here

- **Model Context Protocol (MCP)** is used to isolate **publishing** (Twitter/LinkedIn API calls, token handling, refresh) from the main backend and LangGraph.  
- The **MCP Publishing Server** is the module that exposes tools `publish_post` and `upload_media`.  
- The **backend** never calls Twitter/LinkedIn directly; it calls the MCP **client** (`mcp_publish.client`), which invokes the same tool logic (in-process) or a remote MCP server (if you run one).

### Flow

1. **OAuth** (handled entirely in FastAPI): User hits /v1/oauth/twitter/start (or linkedin). Backend redirects to provider, then callback; backend stores tokens in `social_connections` (encrypted). No MCP involved in OAuth.

2. **Publishing (after HITL approval)**  
   - Graph runs `upload_image` (if image approved) → `call_upload_media("twitter", base64, user_id, connection_id, image_url)` and same for LinkedIn → `media_ids` stored in state.  
   - Then `publish_twitter` → `call_publish_post("twitter", text, user_id, connection_id, media_id, None)`.  
   - Then `publish_linkedin` → `call_publish_post("linkedin", text, user_id, connection_id, None, metadata)` with `linkedin_asset_urn` from upload.  
   - MCP layer (server’s tool impl): resolves `connection_id` (or default via get_default_connection_id), loads tokens from DB, calls Twitter/LinkedIn APIs, refreshes token on 401 if needed, returns post_id/status or media_id/error.

### Usage

- **In-process (default):** Backend and “MCP” run in the same process. `mcp_publish.client` imports `mcp_publish.server._get_tools_impl` and calls the returned functions. No separate server needed.  
- **Separate MCP server (optional):** Run `python -m mcp_publish.server` (e.g. on port 8001). Then the backend’s MCP client would need to be wired to call that server over the network (Streamable HTTP or other transport). Current code path is in-process.  
- **Credentials:** Same `.env` and same SQLite DB as backend. MCP only reads connection tokens and config; it does not perform OAuth.

### Why It Exists

- **Separation of concerns:** LangGraph and FastAPI stay free of platform-specific API and token-refresh logic.  
- **Security:** Tokens are read only inside the MCP layer and DB; publish nodes never see raw tokens.  
- **Extensibility:** New platforms can be added in the MCP server without changing graph nodes beyond passing platform and metadata.

---

## 7. Data Flow & Persistence

- **Execution lifecycle:** Create execution → insert DB row (execution_id, user_id, url, initial_state, idempotency_key). Background task runs graph. On interrupt, API saves `state_json` and status (e.g. awaiting_human). On submit_actions, API restores state into graph if needed, invokes with Command(resume=...), then saves updated state and status again.  
- **Inbox:** GET /v1/inbox returns executions with status in (`awaiting_human`, `awaiting_auth`) for the current user.  
- **Checkpoints:** LangGraph uses AsyncSqliteSaver (or MemorySaver) keyed by `thread_id` = execution_id. So a resumed run has full graph state; DB state_json is the source of truth when checkpointer is missing (e.g. after restart).  
- **Connections:** Stored in social_connections (user_id, provider, account_id, display_name, label, encrypted_json, expires_at, is_default). Tokens decrypted only when needed (publish, refresh).  
- **Idempotency:** Same URL + user while status in (running, awaiting_human, awaiting_auth) returns existing execution instead of creating a new one.

---

## 8. Authentication & Security

- **App auth:** Email/password signup and login; JWT (Bearer) for /v1/auth/me, /v1/executions, /v1/connections, /v1/oauth/start.  
- **Tokens at rest:** SocialConnection.encrypted_json encrypted with Fernet (TOKENS_FERNET_KEY).  
- **OAuth:** PKCE for Twitter; state stored in oauth_states; callback exchanges code and stores tokens in SocialConnection.  
- **Publish:** No tokens in graph state; MCP layer reads from DB by user_id and connection_id.

---

## 9. Quick Reference: APIs & Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | /v1/auth/signup | Register (email, password) → token. |
| POST | /v1/auth/login | Login → token. |
| GET | /v1/auth/me | Current user (Bearer). |
| GET | /v1/connections | List social connections (Bearer). |
| DELETE | /v1/connections/{id} | Remove connection (Bearer). |
| PATCH | /v1/connections/{id} | Update label / set default (Bearer). |
| GET | /v1/oauth/twitter/start?user_id= | Start Twitter OAuth. |
| GET | /v1/oauth/twitter/callback | Twitter callback. |
| GET | /v1/oauth/linkedin/start?user_id= | Start LinkedIn OAuth. |
| GET | /v1/oauth/linkedin/callback | LinkedIn callback. |
| POST | /v1/executions | Create execution { url } (Bearer). |
| GET | /v1/inbox | List executions awaiting_human/awaiting_auth (Bearer). |
| GET | /v1/executions/{id} | Get execution state (Bearer). |
| POST | /v1/executions/{id}/actions | Submit HITL actions and resume (Bearer). |
| GET | /v1/proxy-image?url= | Backend image fetch (CORS workaround) (Bearer). |

---

This README is maintained in **`insights/`** and should be updated when the workflow, file roles, or MCP usage change.
