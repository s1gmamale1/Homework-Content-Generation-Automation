# syntax=docker/dockerfile:1.7

# Stage 1: build the React SPA into web/dist
FROM node:22-alpine AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: resolve Python dependencies with uv into a venv
FROM python:3.13-slim AS py-deps
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Stage 3: runtime image
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --shell /bin/bash app

WORKDIR /app

COPY --from=py-deps /app/.venv /app/.venv

COPY main.py alembic.ini ./
COPY app ./app
COPY alembic ./alembic
COPY prompts ./prompts
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

COPY --from=web-build /web/dist ./web/dist

# `sed` strips any CR so a Windows (CRLF) checkout can't break the
# `#!/usr/bin/env sh` shebang inside the Linux image (env: 'sh\r': not found).
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
 && chmod +x /usr/local/bin/docker-entrypoint.sh \
 && chown -R app:app /app
USER app

# Build identity. This image ships no `.git` (the build context excludes it)
# and no git binary, so nothing inside the container can work out which commit
# it was built from — the build has to say so. `app/api/v1/regeneration.py`
# reads this when stamping a campaign's immutable `app_git_revision`, and
# refuses to create one when no source can name a revision.
#
# CI binds it to the built commit (`--build-arg APP_GIT_REVISION=${{ github.sha }}`
# in .github/workflows/docker-publish.yml). The default is empty so a manual
# `docker build` still works; an empty value reads as ABSENT, never as a
# revision. Declared last on purpose: changing the revision then reuses every
# cached layer above instead of rebuilding the image.
ARG APP_GIT_REVISION=""
ENV APP_GIT_REVISION=${APP_GIT_REVISION}

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]