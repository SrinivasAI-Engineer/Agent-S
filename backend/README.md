# Backend (FastAPI + LangGraph)

## What this service does

- Accepts URL ingestion requests (article/blog only)
- Scrapes content via **FireCrawl**
- Analyzes content relevance
- Generates platform-specific drafts (Twitter + LinkedIn)
- Selects an image strictly from the article assets
- **Interrupts** for human review in an Agent Inbox
- Routes conditionally based on granular HITL actions
- Verifies OAuth tokens; **publishing is delegated to the MCP Publishing Server**
- Checkpoints state at each interrupt; supports resume

## Tech

- FastAPI
- LangGraph
- SQLite (default) for executions, tokens, and checkpoints
- FireCrawl API (scrape)
- **MCP Publishing Server** (Twitter, LinkedIn) — see `mcp_publish/README.md`

## MCP Publishing Server (modular publishing)

Platform-specific publishing (Twitter, LinkedIn) is handled by a separate **MCP server**, not by direct API calls inside LangGraph.

- **LangGraph** keeps the same nodes and HITL flow; publish nodes call **MCP tools** (`publish_post`, `upload_media`) instead of calling platform APIs.
- **OAuth** and token storage stay in this backend (FastAPI); the MCP server **reads** tokens from the shared DB and performs API calls, retries, and token refresh.
- **Clear README for creating and running the MCP server:** [`mcp_publish/README.md`](mcp_publish/README.md) — tools, setup, run, and how the backend/LangGraph use it.

## Setup

### 1) Install

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2) Configure environment

Copy `.env.example` to `.env` and fill values.

```bash
copy .env.example .env
```

You must set:

- `TOKENS_FERNET_KEY` (for encrypted token storage)
- `FIRECRAWL_API_KEY` (for scraping)
- Twitter + LinkedIn OAuth credentials if you want publishing enabled

### 3) Run

```bash
uvicorn app.main:app --reload --port 8000
```

## Core endpoints (high level)

- `POST /v1/executions` — create a new execution from `{ user_id, url }`
- `GET /v1/inbox` — list executions waiting for HITL
- `GET /v1/executions/{execution_id}` — view full state snapshot
- `POST /v1/executions/{execution_id}/actions` — submit HITL actions and resume graph
- `GET /v1/oauth/{provider}/start` — start OAuth (twitter/linkedin)
- `GET /v1/oauth/{provider}/callback` — OAuth callback (stores tokens)

## OAuth quickstart (local)

Open in a browser:

- Twitter: `http://localhost:8000/v1/oauth/twitter/start?user_id=YOUR_USER_ID`
- LinkedIn: `http://localhost:8000/v1/oauth/linkedin/start?user_id=YOUR_USER_ID`

After successful login, you’ll be redirected back to the frontend. Tokens are stored encrypted in SQLite.

## HITL action rules (enforced)

- **Reject Content** → terminate immediately
- **Approve Content + Approve Image** → continue, upload image, publish
- **Approve Content + Reject Image** → continue, publish text only
- **Reject Image only** → continue, publish text only (does not terminate)
- **Regenerate Twitter** → route back to generate Twitter only
- **Regenerate LinkedIn** → route back to generate LinkedIn only
- **Edit Twitter** → update only Twitter post text
- **Edit LinkedIn** → update only LinkedIn post text

## Notes on tokens

- Tokens are obtained via OAuth endpoints (outside LangGraph).
- At publish time, the graph does **not** read or store tokens; the **MCP Publishing Server** does (it reads from the shared DB and refreshes as needed).
- Tokens are stored encrypted-at-rest (Fernet key in env).

## MCP server creation and run

See **[`mcp_publish/README.md`](mcp_publish/README.md)** for:

- What the MCP server does and which tools it exposes
- How to run it (`python -m mcp_publish.server`)
- Env and DB requirements (same as backend)
- How LangGraph/backend interact with the MCP server

## Persistence and resumability

- **Execution state** is stored in the DB (`state_json`, `status`). It is saved on interrupt (awaiting_human / awaiting_auth), on submit_actions, and on timeout/error.
- **Inbox** is always read from the DB (`list_inbox`). After a page reload or server restart, the inbox is still populated; paused agents do not disappear.
- **LangGraph checkpoints**: On startup, the app uses **SqliteSaver** (when available) so checkpoints persist in `agentsocials.checkpoints.db`. If that fails, it falls back to in-memory checkpoints; resume still works by restoring state from the DB when the checkpointer has no checkpoint for that thread.
- **Resume**: `POST /executions/{id}/actions` restores state from the DB into the graph when needed, then resumes. Agents are resumable anytime.

## Full migration doc (MCP + persistence)

For a single place describing **all** updates (MCP architecture, LangGraph ↔ MCP interaction, persistence strategy, file-level changes), see:

**[`docs/MIGRATION_MCP_AND_PERSISTENCE.md`](docs/MIGRATION_MCP_AND_PERSISTENCE.md)**

