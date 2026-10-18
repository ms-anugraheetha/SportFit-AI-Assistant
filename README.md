---
title: SportFit AI Assistant (academic prototype)
emoji: 🏋️
colorFrom: gray
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# SportFit AI Assistant: customer chat + staff dashboard

> **Independent academic prototype.** This project is **not affiliated with, endorsed by, or connected to SportFit** (https://sportfitkw.com/). It was built by a student for a course. It has no access to SportFit's systems, never contacts SportFit, and every request it records is **demonstration data in this project's own database**. Please do not enter real personal or sensitive information.

**Public demo URL:** _not deployed yet_ (this line is updated only after a deployment has been opened and tested; see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

## What it does

**Customer chat** (`/`)
- Answers service questions from a small curated knowledge base with source links and a retrieval date. It never invents prices, warranties, stock, schedules or contact details. When it cannot verify something, it says so and offers a human-support handoff.
- Collects a repair request step by step, validates every field on the server, shows a summary, and saves the record **only after the customer types an explicit confirmation**. The reference is shown only after the database commit.
- Tracks a demo request by reference and shows limited status information (never email, phone, serial number or free text).
- Gives conservative equipment-safety guidance (stop using damaged equipment, no bypassing safety features, no opening electrical parts).

**Staff dashboard** (`/staff`)
- Login with a single staff account configured through environment variables (argon2 password hash, server-side sessions, CSRF protection).
- Status counts, search, status filter, sort by date, pagination, request detail, and a status dropdown with a recorded history (old status, new status, staff user, time).
- A status change made by staff is what the customer's tracking shows next, because both read the same PostgreSQL rows.

## Architecture

```mermaid
flowchart LR
  subgraph Browser
    C[Customer chat /]
    S[Staff dashboard /staff]
  end
  subgraph Container["One Docker container (FastAPI)"]
    API[FastAPI app<br/>validation, rate limits,<br/>staff auth + CSRF]
    ORCH[Chat orchestrator<br/>state machine + confirmation]
    AGENT[Agent loop<br/>max 4 tool calls]
    MCPC[MCP client]
    MCPS[MCP server<br/>stdio subprocess]
    KB[(Knowledge base JSON)]
  end
  DB[(External PostgreSQL)]
  HF[Hugging Face<br/>Inference Providers]

  C -->|/api/chat, tracking| API
  S -->|/api/staff/* cookie + CSRF| API
  API --> ORCH
  ORCH -->|general questions only| AGENT
  AGENT <-->|chat + tool calls| HF
  AGENT --> MCPC
  ORCH -->|confirmed create| MCPC
  MCPC <-->|MCP over stdio| MCPS
  MCPS --> KB
  MCPS --> DB
  API -->|staff list, detail, status| DB
```

How to read it: the **language model only handles general questions** and may call four read-only tools through MCP. Everything that changes data is ordinary backend code. Staff status updates go through separate authenticated endpoints and are **not** MCP tools, so the model cannot reach them.

## The MCP integration (for the class explanation)

- `backend/app/mcp_server.py` is a real MCP server built with the official Python SDK (`FastMCP`). It exposes five tools: `search_services`, `search_knowledge_base`, `get_service_request`, `get_handoff_guidance`, `create_service_request`.
- `backend/app/mcp_client.py` is a real MCP client. It starts the server as a **stdio subprocess**, discovers the tools through the protocol (`list_tools`), and calls them with validated arguments. A supervisor restarts the subprocess if it fails.
- The model is offered only the four read-only tools. `create_service_request` is called by the application itself, only in the confirmed state. The subprocess receives only `DATABASE_URL`, never `HF_TOKEN` or staff secrets.
- The model never sees the repair-form details a customer types; a deterministic state machine handles them.

## Tech stack

React + Vite + TypeScript (plain CSS) · FastAPI · Pydantic · SQLAlchemy + Alembic · PostgreSQL · official Python MCP SDK · OpenAI-compatible client pointed at Hugging Face Inference Providers · pytest, Vitest, Playwright (browser check) · Docker (Hugging Face Docker Space).

## Run it locally

You need Python 3.10+ (3.12 recommended), Node.js 20+ (LTS), and a PostgreSQL database.

```powershell
# 1. Python environment (outside OneDrive is fine; any folder works)
python -m venv "$HOME\sportfit-venv"
& "$HOME\sportfit-venv\Scripts\python.exe" -m pip install -r backend\requirements.txt

# 2. Settings: copy the template, then edit backend\.env (never commit it)
Copy-Item .env.example backend\.env
cd backend
& "$HOME\sportfit-venv\Scripts\python.exe" -m app.cli gen-secret          # paste into SESSION_SECRET
& "$HOME\sportfit-venv\Scripts\python.exe" -m app.cli hash-password       # paste into STAFF_PASSWORD_HASH (keep the single quotes)

# 3. Create the tables
& "$HOME\sportfit-venv\Scripts\python.exe" -m alembic upgrade head

# 4. Build the frontend once, copy it where FastAPI serves it
cd ..\frontend
npm install
npm run build
Copy-Item -Recurse -Force dist ..\backend\static

# 5. Start the app, then open http://127.0.0.1:8000  (staff: /staff)
cd ..\backend
& "$HOME\sportfit-venv\Scripts\python.exe" -m uvicorn app.main:app --port 8000
```

For front-end development with hot reload, run the backend as above and `npm run dev` in `frontend` (http://localhost:5173, proxied to port 8000). Keep `APP_ENV=development` locally: in `production` the session cookie is marked `Secure` and browsers will not store it over plain `http://`.

PostgreSQL: any local install or a free hosted database works. `DATABASE_URL` accepts `postgresql://`, `postgres://` or `postgresql+psycopg://` forms. Without `HF_TOKEN` the chat still works, answering from the verified knowledge base only (the UI says so).

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | PostgreSQL connection string. Secret. |
| `HF_TOKEN` | for the live model | Hugging Face token with permission to call Inference Providers. Secret. |
| `HF_MODEL` | no | Model id (default `openai/gpt-oss-120b`; `:provider` suffix pins a provider). Must support tool calling. |
| `HF_BASE_URL` | no | Default `https://router.huggingface.co/v1`. |
| `STAFF_USERNAME` | yes for staff | Staff login name (default `staff`). |
| `STAFF_PASSWORD_HASH` | yes for staff | argon2 hash from `python -m app.cli hash-password`. Secret. Staff login is disabled if missing. |
| `SESSION_SECRET` | yes for staff | At least 32 random characters (`python -m app.cli gen-secret`). Secret. |
| `APP_ENV` | no | `development` (default) or `production` (Secure cookies, API docs off). |
| `TRUST_PROXY_HEADERS` | no | `true` only behind a trusted proxy such as a Hugging Face Space (set in the Dockerfile). |
| `CORS_ORIGINS` | no | Only needed if the frontend is served from another origin. |
| `RATE_LIMIT_*_PER_MINUTE` | no | Per-IP limits for chat, tracking, create, login. |

`.env.example` contains placeholders only. A model check that needs your real token is in `backend/scripts/verify_hf.py`.

## Tests

```powershell
cd backend
$env:TEST_DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/sportfit_test"   # a THROWAWAY database
& "$HOME\sportfit-venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
& "$HOME\sportfit-venv\Scripts\python.exe" -m pytest -q                       # database tests need TEST_DATABASE_URL
$env:HF_TOKEN = "hf_..."; & "$HOME\sportfit-venv\Scripts\python.exe" -m scripts.verify_hf   # real model check, needs token + credits
cd ..\frontend; npm test                                                      # frontend unit tests
```

- **Needs PostgreSQL** (`TEST_DATABASE_URL`): schema/migrations, request creation, tracking, staff auth, dashboard API, MCP-with-database tests. The test run drops and recreates tables in that database, so never point it at real data.
- **Needs a Hugging Face token and credits:** `scripts.verify_hf` and `pytest -m needs_hf`. These are the only checks that say anything about the real model. The deterministic agent tests use a scripted fake model and do **not** prove the real model works.
- Browser end-to-end check: `e2e/run_e2e.py` (Playwright; see the file header).

## Security and privacy notes

- Staff secrets and the Hugging Face token exist only as server environment variables or platform secrets; none is in the repository or the frontend.
- Staff password: argon2 hash; wrong user and wrong password return the same 401; login is rate-limited. Sessions are random tokens in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` in production); only an HMAC of the token is stored, so logout and expiry are enforced on the server (8 h).
- CSRF: every staff POST/PATCH needs the per-session `X-CSRF-Token` header (403 otherwise). Unauthenticated staff requests get 401.
- The model cannot change request status or create records by itself. Creation needs explicit confirmation tracked by the server's state machine; a crafted message such as "submit without confirmation" does not bypass it (tested).
- Public tracking returns only reference, status, equipment type and dates. Unknown and malformed references both return the same 404.
- Inputs: Pydantic validation on every request, length limits, request-size limit, per-IP rate limits, ORM queries only (no string-built SQL; search wildcards are escaped), generic 500 errors, no customer messages or personal data in logs.
- Security headers and a Content-Security-Policy on the page; API responses are `no-store`.

## Known limitations

- Prototype scale: one staff account, in-memory conversation state and rate limits (they reset on restart and are per container), no email or notifications, no technician scheduling.
- The knowledge base is small and hand-curated from public pages read on 2026-10-17. Spare parts, pricing, warranty and contact details are deliberately **not verified** and the assistant says so.
- The assistant gives general safety guidance only and never diagnoses equipment.
- A hosted model can be slow, rate-limited or out of credits; the app then falls back to knowledge-base answers and labels them as such.
- Tracking references are unguessable but are not a login: anyone who has a reference can see its limited status.
- Free hosting can sleep or restart; the free database may have limits.
- No licence has been added yet (a licence should be chosen only after confirming the code and assets can legally be shared under it).

## Embedding the widget in a real business site (future, with the owner's permission)

This project must not be placed on SportFit's real website without their explicit permission. If the owner agreed, the chat page could be embedded as an `<iframe>` pointing at the deployed app (the Content-Security-Policy `frame-ancestors` list would need to include the owner's domain), while `/staff` would stay a separate, protected address. Nothing here touches the live site.

## More documents

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): Hugging Face Space and external database steps, secrets list
- [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md): 5-minute classroom demonstration
- [docs/CHECKLIST.md](docs/CHECKLIST.md): manual setup checklist
- [docs/EXPLAIN.md](docs/EXPLAIN.md): short architecture / security / limitations explanation for presenting
- Screenshots: [docs/screenshots](docs/screenshots) (taken from a local test run with demo data)
