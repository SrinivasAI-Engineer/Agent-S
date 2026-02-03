# MCP Publishing Server

Modular **Model Context Protocol (MCP)** server that handles all platform-specific publishing for AgentSocialS. LangGraph and the main backend **never call Twitter/LinkedIn APIs directly**; they call this MCP server’s tools instead.

---

## What this server does

- **Exposes two tools:** `publish_post` and `upload_media`.
- **Uses credentials from the shared DB** (same SQLite as the main backend). OAuth flows stay in the FastAPI backend; this server only **reads** tokens and **calls** platform APIs.
- **Handles retries and token refresh** (e.g. Twitter 401 → refresh → retry) inside the server so LangGraph does not deal with tokens.

---

## Tools

### 1. `publish_post`

Publishes a post to one platform.

| Parameter       | Type   | Required | Description |
|----------------|--------|----------|-------------|
| `platform`    | string | Yes      | `"twitter"` \| `"linkedin"` |
| `text`        | string | Yes      | Post body (plain text). |
| `user_id`     | string | Yes      | Current user id (for resolving default connection). |
| `connection_id` | int  | No       | Specific connection id; if omitted, default for that platform is used. |
| `media_id`    | string | No       | Platform media id (e.g. Twitter `media_id_string`, LinkedIn asset URN). |
| `metadata`    | string | No       | JSON string; e.g. `{"linkedin_asset_urn": "..."}` for LinkedIn. |

**Returns (JSON string):**

- `{ "post_id": "...", "status": "success" }` on success.
- `{ "post_id": "", "status": "failure", "error": "..." }` on failure.

---

### 2. `upload_media`

Uploads media (image) for a platform so it can be attached to a post.

| Parameter       | Type   | Required | Description |
|----------------|--------|----------|-------------|
| `platform`    | string | Yes      | `"twitter"` \| `"linkedin"` |
| `media_base64`| string | Yes      | Raw image bytes, base64-encoded. |
| `user_id`     | string | Yes      | Current user id. |
| `connection_id` | int  | No       | Specific connection; if omitted, default is used. |
| `image_url`   | string | No       | Optional source URL (for logging/debug). |

**Returns (JSON string):**

- `{ "media_id": "..." }` on success (e.g. Twitter `media_id_string`, LinkedIn asset URN).
- `{ "media_id": "", "error": "..." }` on failure.

---

## Prerequisites

- **Python 3.10+** (same as backend).
- **Backend env and DB:** Use the same `.env` and SQLite DB as the main backend so this server can read `SocialConnection` and config (Twitter/LinkedIn env vars).
- **MCP SDK:** `pip install mcp` (or add `mcp` to backend `requirements.txt` and install from backend).

---

## How to run the MCP server

From the **backend** directory (so that `app` and `.env` are in scope):

```bash
cd backend
.venv\Scripts\activate
pip install mcp
python -m mcp_publish.server
```

By default it serves over **Streamable HTTP** on **port 8001** (configurable in `mcp_publish/server.py` if needed).

- **URL:** `http://localhost:8001` (or the host/port you set). The MCP endpoint is at **`/mcp`** (e.g. `http://localhost:8001/mcp`).
- **Transport:** Streamable HTTP (MCP standard). Clients (e.g. LangGraph/backend) call tools via this transport.

---

## Using the standalone server (remote mode)

To have the **main backend** call this server over HTTP instead of in-process:

1. **Start the MCP server** in a separate terminal (from `backend`):
   ```bash
   cd backend
   python -m mcp_publish.server
   ```
   Leave it running (e.g. on port 8001).

2. **Configure the backend** to use the standalone server by setting the URL in `backend/.env`:
   ```env
   MCP_PUBLISH_URL=http://localhost:8001
   ```
   If the server runs on another host/port, set the full base URL (e.g. `http://mcp-host:8001`). The client will use the `/mcp` path automatically when no path is given.

3. **Start the main backend** as usual (e.g. `uvicorn app.main:app --reload`). Publish nodes will call the standalone MCP server over HTTP instead of importing `mcp_publish.server` in-process.

- **In-process (default):** If `MCP_PUBLISH_URL` is not set or empty, the backend uses in-process calls (same as before); you do **not** need to run `python -m mcp_publish.server` separately.
- **Standalone:** Set `MCP_PUBLISH_URL` and run the MCP server as a separate process when you want to scale or isolate publishing (e.g. different machine, different env).

---

## Configuration

The server reuses the backend’s config and DB:

- **Database:** Same `database_url` / `agentsocials.db` as the backend (from backend `.env` or `app.config.settings`). It only **reads** connection rows (no OAuth writes).
- **Env vars:** Same as backend for platforms:
  - Twitter: `TWITTER_CLIENT_ID`, `TWITTER_CLIENT_SECRET`, etc.
  - LinkedIn: `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, etc.

No separate config file is required; run from `backend` and use the same `.env`.

---

## How the backend / LangGraph use this server

1. **Main backend** (FastAPI) keeps doing OAuth and storing tokens in the DB; it does **not** call platform APIs for publishing.
2. **LangGraph** publish nodes (e.g. `publish_twitter`, `publish_linkedin`) call **`mcp_publish.client`** only:
   - **In-process (default):** When `MCP_PUBLISH_URL` is not set, the client imports `mcp_publish.server` and calls the tool functions directly.
   - **Standalone:** When `MCP_PUBLISH_URL` is set, the client sends JSON-RPC over HTTP to the running MCP server (e.g. `http://localhost:8001/mcp`).
   - For **upload:** `call_upload_media(...)` → get `media_id`.
   - For **publish:** `call_publish_post(...)` → get `post_id` and `status`.
3. **Credentials:** LangGraph and publish nodes do **not** read or store tokens; only the MCP server (and the backend OAuth flow) do. The MCP server resolves `user_id` / `connection_id` and loads tokens from the shared DB.

So: **creation of the MCP server** = this package (`mcp_publish/`) plus running it with `python -m mcp_publish.server`. The README you’re reading is the “clear README for this MCP server creation.”

---

## Project layout

```
backend/
  mcp_publish/
    __init__.py    # Exposes run
    server.py      # FastMCP app, tools publish_post & upload_media, run()
    README.md      # This file
  app/
    ...
```

- **Adding a new platform:** Implement the platform branch inside `publish_post` (and optionally `upload_media`) in `server.py`, and add the same env/config the backend would use. No change to LangGraph nodes except passing the new `platform` value and any new metadata.

---

## Quick checklist for “MCP server creation”

1. **Create** the `mcp_publish` package under `backend/` (this repo already has it).
2. **Install** MCP: `pip install mcp`.
3. **Reuse** backend `.env` and DB; run from `backend`: `python -m mcp_publish.server`.
4. **Confirm** the server is up (e.g. MCP Inspector or your backend’s MCP client pointing at `http://localhost:8001`).
5. **Wire** LangGraph publish nodes to call this server’s tools via an MCP client instead of calling platform APIs directly.

For full architecture (LangGraph ↔ MCP, persistence, auth), see the main **backend README** and the root **README**.

---

## Creating the MCP server from scratch (step-by-step)

If you are building this MCP server in a new repo or from zero:

1. **Create the package** — Under your backend root (e.g. `backend/`), create a folder `mcp_publish/` with `__init__.py` and `server.py` (see this repo's `server.py` for the full implementation).

2. **Install the MCP SDK** — In the same environment as your backend: `pip install mcp`. Optionally add `mcp` to `backend/requirements.txt`.

3. **Define the tools** — Use FastMCP: `from mcp.server.fastmcp import FastMCP` then `mcp = FastMCP("AgentSocialS Publishing", json_response=True)`. Register tools with `@mcp.tool()` on async functions that accept the parameters listed above (`publish_post`, `upload_media`). Inside those functions, use your existing DB and config (same as backend) to load tokens and call Twitter/LinkedIn APIs.

4. **Run the server** — From the backend directory: `python -m mcp_publish.server`. In `server.py`, use `mcp.run(transport="streamable-http", ...)` with the desired host/port (e.g. port 8001 so it does not clash with FastAPI on 8000).

5. **Connect the backend** — In your LangGraph publish nodes, replace direct API calls with MCP client calls to `publish_post` and `upload_media` (same arguments and return shapes as in this README).

The **clear README for this MCP server creation** is this file: `backend/mcp_publish/README.md`. Keep it next to the code so anyone can run and extend the server from here.
