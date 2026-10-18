# One container: FastAPI serves the API, the built React app, and manages the MCP server subprocess.
# Built for a Hugging Face Docker Space (port 7860, container user id 1000). PostgreSQL is EXTERNAL.

# ---- stage 1: build the frontend
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- stage 2: backend runtime
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH
WORKDIR $HOME/app/backend

COPY --chown=user backend/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY --chown=user backend/ ./
COPY --from=web --chown=user /web/dist ./static

# Behind the Space's HTTPS proxy: real client IPs for rate limiting, Secure cookies, docs disabled.
ENV APP_ENV=production TRUST_PROXY_HEADERS=true
EXPOSE 7860

# Applies database migrations, then starts the server. Secrets (DATABASE_URL, HF_TOKEN, STAFF_PASSWORD_HASH,
# SESSION_SECRET) come from the Space's runtime secrets, never from the image.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 7860 --proxy-headers --forwarded-allow-ips='*'"]
