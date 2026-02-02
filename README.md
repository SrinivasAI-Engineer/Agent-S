# AgentSocialS — Autonomous Social Media Agent (LangGraph + HITL)

Production-grade **Human-in-the-Loop** agent that converts **article/blog URLs** into platform-specific posts for:

- Twitter (X)
- LinkedIn

It uses **LangGraph** (state machine + interrupts + checkpoints) and **never publishes without explicit human approval**.

## Repo structure

- `backend/` — FastAPI service, LangGraph workflow, FireCrawl scraping, OAuth/token storage, publishing
- `frontend/` — React “Agent Inbox” UI for review/edit/approve/reject/regenerate

## Quickstart

### 1) Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Backend runs at `http://localhost:8000`.

### 2) Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at `http://localhost:5173`.

## Key guarantees (do not violate)

- **No auto-posting**: publishing nodes only run when HITL actions explicitly approve content.
- **No external/AI/stock images**: images are selected strictly from the scraped article assets.
- **No image upload before approval**: upload step only happens after HITL approval and routing.
- **No platform UI redirects at publish time**: authentication happens outside graph; graph interrupts if tokens missing/expired.

## Documentation

- Backend details: `backend/README.md`
- **MCP Publishing Server (creation, tools, run):** `backend/mcp_publish/README.md`
- **Migration (MCP + persistence, all updates):** `backend/docs/MIGRATION_MCP_AND_PERSISTENCE.md`
- Frontend details: `frontend/README.md`

