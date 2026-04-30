from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger as log

from app.api.v1 import api_v1_router
from app.config import settings
from app.db import SessionLocal
from app.log import configure as configure_logging
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import phase_outputs as phase_repo
from app.services.prompts import load_all as load_prompts

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_prompts()
    log.info("Prompts loaded")

    async with SessionLocal() as session:
        for b in await books_repo.list_running_for_sweep(session):
            await books_repo.set_status(
                session, b.id, "failed",
                error_message="orphaned: worker restarted",
            )
        for j in await jobs_repo.list_running_for_sweep(session):
            await jobs_repo.set_status(
                session, j.id, "failed",
                completed_at=datetime.now(timezone.utc),
                error_message="orphaned: worker restarted",
            )
        for p in await phase_repo.list_running_for_sweep(session):
            await phase_repo.set_status(
                session, p.id, "failed",
                completed_at=datetime.now(timezone.utc),
                error_message="orphaned: worker restarted",
            )
        await session.commit()
    log.info("Orphan sweep complete")
    yield


app = FastAPI(
    title="Edu-Homework",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allow_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router)


@app.get("/health")
async def root_health() -> dict:
    return {"status": "ok"}


# ─── SPA mount ─────────────────────────────────────────────────────────
# Serves the built React app from web/dist if present. The SPA fallback
# (catch-all) returns index.html so client-side routes like /book/:id work
# on direct URL access. The mount is conditional so dev-only setups (where
# the SPA runs on Vite's :5173 with proxy) don't fail to start.

WEB_DIST = Path(__file__).resolve().parent / "web" / "dist"

if WEB_DIST.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=WEB_DIST / "assets"),
        name="spa-assets",
    )

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon() -> Response:
        return FileResponse(WEB_DIST / "favicon.svg")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str, request: Request) -> Response:
        # Reserved prefixes are handled by their own routes; everything else
        # serves the SPA shell so React Router can resolve the path.
        if full_path.startswith(("api/", "health", "docs", "openapi.json", "assets/")):
            return Response(status_code=404)
        index_path = WEB_DIST / "index.html"
        if not index_path.is_file():
            return Response(status_code=404)
        return FileResponse(index_path)
