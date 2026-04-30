import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_v1_router
from app.config import settings
from app.db import SessionLocal
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import phase_outputs as phase_repo
from app.services.prompts import load_all as load_prompts

log = logging.getLogger("edu-homework")
logging.basicConfig(level=logging.INFO)


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


frontend_dir = Path(__file__).resolve().parent / "frontend"
if frontend_dir.is_dir():
    app.mount("/ui", StaticFiles(directory=frontend_dir, html=True), name="ui")
