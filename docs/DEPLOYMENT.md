# Deployment guide (Hugging Face Docker Space + external PostgreSQL)

> Status: the Dockerfile has **not been built on a Docker daemon yet** by the author of these files, and nothing is deployed. What *was* checked: a clean Python 3.12 environment installing `backend/requirements.txt`, running `alembic upgrade head` against an empty PostgreSQL, and starting the app with the production settings and the built frontend. The first real Space build is the true test. Do not put a demo URL in the README until you have opened it and completed the workflow below.

Hugging Face's Docker Space documentation says: set `sdk: docker` in the README front matter, the default port is 7860 (`app_port` changes it), the container runs as user id 1000, secrets are available as environment variables at runtime, and the disk is not persistent. This project follows that (see the `Dockerfile` and the README header).

## 0. Optional but recommended: test the Docker image on your own computer first

This builds the same `Dockerfile` the Space uses and runs it with a throwaway local PostgreSQL (`docker-compose.yml`). It is how you find Dockerfile problems before the Space build.

1. Install Docker Desktop (`winget install -e --id Docker.DockerDesktop`), restart if asked, start Docker Desktop and wait until it says it is running.
2. Make sure `backend\.env` has `HF_TOKEN`, `SESSION_SECRET` and `STAFF_PASSWORD_HASH` filled in (see the README).
3. From the project folder: `docker compose up --build`. The first build takes several minutes.
4. Open http://localhost:8000 (customer) and http://localhost:8000/staff. The compose file forces its own local database and `APP_ENV=development`, whatever `backend\.env` says.
5. Stop with `Ctrl+C` then `docker compose down` (`docker compose down -v` also deletes the local data).

The local compose database is **not** what the public Space uses; the Space needs the external database below.

## 1. Create the external PostgreSQL database

A Space does not keep files between restarts, so the data must live in an external database. Any hosted PostgreSQL works (for example Neon or Supabase free tiers).

1. Create a project and a database; copy the **connection string** (it looks like `postgresql://user:password@host/dbname?sslmode=require`).
   Alternative with the Neon CLI: `neon init -y --org-id <ORG_ID> --project-id <PROJECT_ID> --branch production --services postgres`, then `neon deploy`, which pulls the connection string into `.env.local`. The app itself needs no Neon-specific code: it only reads `DATABASE_URL`.
   Prefer the **direct (non-pooled)** connection string for this app. It has not been tested against Neon's pooled endpoint.
   After setting `DATABASE_URL`, run `python -m alembic upgrade head` and then `python -m scripts.check_db` (in `backend`) to prove the connection and a real write/read round trip.
2. Keep it private. It becomes the `DATABASE_URL` secret. The app converts `postgresql://` and `postgres://` forms automatically.

## 2. Create the Hugging Face token

huggingface.co/settings/tokens → new **fine-grained** token with permission **Make calls to Inference Providers**. Check billing/credits at huggingface.co/settings/billing: HTTP 402 errors mean credits are used up. Run `python -m scripts.verify_hf` locally first.

## 3. Create the staff credentials

Locally, in the `backend` folder:

```
python -m app.cli gen-secret        # -> SESSION_SECRET
python -m app.cli hash-password     # -> STAFF_PASSWORD_HASH
```

Choose a strong password (12+ characters) and keep it out of the repository. In the Space secrets paste the hash **without** surrounding quotes.

## 4. Create the Space

1. huggingface.co → New → Space → SDK **Docker** (blank template) → visibility **Public**.
2. Settings → **Variables and secrets** → add these as **Secrets**: `DATABASE_URL`, `HF_TOKEN`, `STAFF_PASSWORD_HASH`, `SESSION_SECRET`. Add as **Variables**: `STAFF_USERNAME` (for example `staff`), `HF_MODEL` (for example `openai/gpt-oss-20b` or `openai/gpt-oss-120b`).
3. Send the code to the Space repository (it builds automatically on every push):

```powershell
git remote add space https://huggingface.co/spaces/YOUR-HF-USERNAME/YOUR-SPACE-NAME
git push space main
```

Git asks for credentials: username = your Hugging Face username, password = a Hugging Face access token with **write** access. Type them only in your terminal.

If the push is rejected because of binary files (the screenshots), tell the assistant; the usual fixes are Git LFS/Xet for `docs/screenshots` or leaving the screenshots only on GitHub.

## 5. Verify the public deployment (from a fresh browser window)

1. Open the Space URL. `/health/ready` should show `database: true` and `mcp: true`.
2. Customer: ask a service question; start a repair request; try to skip confirmation (it must refuse); confirm; copy the reference; track it.
3. Staff: open `/staff`, sign in, find the request, change its status.
4. Customer: track the reference again and see the new status.
5. Sign out, reload `/staff`: you must see the login screen.

Only after all of these pass should the README's demo-URL line be updated.

## Controlled access for the instructor

Never write staff credentials in the repository or README. Send the instructor the staff username and password privately (message or email). Better: create a separate password just for the demonstration and change the `STAFF_PASSWORD_HASH` secret afterwards.

## Clearing demo records

From the `backend` folder, with `DATABASE_URL` pointing at the database to clean:

```
python -m app.cli clear-demo-data --yes
```

This deletes every request, status history row and staff session. It refuses to run without `--yes`.

## Required secrets and variables

| Name | Kind |
|---|---|
| `DATABASE_URL` | secret |
| `HF_TOKEN` | secret |
| `STAFF_PASSWORD_HASH` | secret |
| `SESSION_SECRET` | secret |
| `STAFF_USERNAME` | variable |
| `HF_MODEL` | variable (optional) |

`APP_ENV=production` and `TRUST_PROXY_HEADERS=true` are already set in the Dockerfile.

## If something fails

- **Space shows "Runtime error" right after start:** open the Logs tab. A migration or database error usually means `DATABASE_URL` is wrong or the database blocks outside connections.
- **`/health/ready` says `database: false`:** check `DATABASE_URL` and the database provider's network rules.
- **Staff login says it is not configured (503):** `STAFF_PASSWORD_HASH` or `SESSION_SECRET` is missing or `SESSION_SECRET` is shorter than 32 characters.
- **Chat says the model is unavailable:** check `HF_TOKEN`, credits and `HF_MODEL`; the knowledge-base fallback keeps working.
- **Staff login works but the dashboard keeps signing you out when the Space is shown inside the huggingface.co page:** open the direct `*.hf.space` URL instead (browsers can block cookies in embedded frames).
