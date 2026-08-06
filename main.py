import asyncio
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

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
from app.repositories import budget as budget_repo
from app.repositories import jobs as jobs_repo
from app.repositories import phase_outputs as phase_repo
from app.services import code_version
from app.services import events_bus
from app.services.prompts import load_all as load_prompts
from app.services.worker import Worker, build_worker_from_settings

configure_logging()


async def _reconcile_on_startup(session) -> None:
    """Boot-time reconcile, scoped to reclaimed jobs (fenced job leases,
    Task 8) — no longer a global force-fail of every pending/running phase
    row.

    - Sweep `books` rows stuck mid-flight when the API last died (unrelated
      to phase/job leasing, kept as-is).
    - Peer-aware startup reclaim (fleet-restart-reclaim-1): if any peer
      worker has a fresh heartbeat, use the full lease window so its
      recently-claimed jobs aren't yanked (avoids double-run + real $ on api
      jobs). If no live peer exists (solo restart), window=0 resets every
      orphaned `running` row immediately (instant single-host recovery
      preserved). Best-effort caveat: on a sub-reclaim_stale_seconds restart
      the old process's own heartbeat row may still read as a live peer ->
      lease path fires, delaying reset by at most one window; this is safe
      (correctness unaffected, recovery just isn't instant).

      `reclaim_orphans_on_startup` already resets the phase rows of every
      job it reclaims (via `reclaim_stuck_jobs` -> `reset_abandoned_phases`),
      so no separate phase sweep is needed here. A job whose `claimed_at`
      isn't stale yet — i.e. a live peer might still own it — is correctly
      left untouched, phases included; it's reclaimed later once it goes
      stale (<= `reclaim_stale_seconds`).
    - Fail `pending` jobs whose attempts are exhausted (also reconciles
      their remaining phase rows to `failed`, terminal).
    """
    for b in await books_repo.list_running_for_sweep(session):
        await books_repo.set_status(
            session, b.id, "failed",
            error_message=phase_repo.ORPHANED_RESTART_MESSAGE,
        )
    n = await jobs_repo.reclaim_orphans_on_startup(
        session, reclaim_stale_seconds=settings.reclaim_stale_seconds
    )
    if n:
        log.info(f"Startup: reclaimed {n} orphaned running job(s) -> pending")
    n_exhausted = await jobs_repo.fail_exhausted_pending_jobs(
        session, max_attempts=settings.queue_max_attempts
    )
    if n_exhausted:
        log.info(
            f"Startup: failed {n_exhausted} attempts-exhausted pending job(s)"
        )
    await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_prompts()
    log.info("Prompts loaded")

    # Reconcile books/jobs/phases stuck mid-flight when the API last died.
    # Scoped to reclaimed jobs only (fenced job leases, Task 8) — a fresh
    # peer-owned job (and its phase rows) is left untouched, not globally
    # force-failed.
    async with SessionLocal() as session:
        await _reconcile_on_startup(session)
    log.info("Orphan sweep complete (books + phase_outputs)")

    # Fleet version floor auto-stamp (fleet-worker-version-gate-1): raise-only —
    # any process starting on newer code fences out every stale worker; a
    # stale-process restart is a no-op. PUT /workers/version-floor is the
    # operator escape hatch (lower/clear).
    if code_version.CODE_VERSION is not None:
        async with SessionLocal() as session:
            raised = await budget_repo.raise_version_floor(
                session,
                version=code_version.CODE_VERSION,
                stamped_by=f"{socket.gethostname()}@{code_version.GIT_SHA or 'unknown'}",
            )
            await session.commit()
        if raised:
            log.info(
                f"Startup: version floor raised to {code_version.CODE_VERSION} "
                f"(sha={code_version.GIT_SHA})"
            )
        else:
            log.info(
                f"Startup: version floor unchanged (own version "
                f"{code_version.CODE_VERSION} <= current floor)"
            )
    else:
        log.warning(
            "Startup: code version undetectable — version floor NOT stamped; "
            "this process is BLOCKED from claiming if a floor is set"
        )

    # Cross-process SSE bus (sse-multipod-1): one LISTEN connection per
    # process routes NOTIFY events into local SSE queues. Deliberately no
    # try/except — a process that can't LISTEN would serve frozen streams,
    # which is the exact bug this bus fixes. The sweep above already proved
    # the DB reachable.
    await events_bus.start_listener()

    # Embedded worker. Set WORKER_CONCURRENCY=0 to disable (e.g., when
    # running standalone workers in separate pods).
    worker: Optional[Worker] = None
    worker_task: Optional[asyncio.Task] = None
    if settings.worker_concurrency > 0:
        worker = build_worker_from_settings()
        worker_task = asyncio.create_task(worker.run(), name="embedded-worker")
        log.info(
            f"Embedded worker started | concurrency={settings.worker_concurrency}"
        )
    else:
        log.info(
            "Embedded worker disabled (WORKER_CONCURRENCY=0); "
            "expecting standalone worker(s) elsewhere"
        )

    try:
        yield
    finally:
        if worker is not None and worker_task is not None:
            worker.stop()
            try:
                await asyncio.wait_for(worker_task, timeout=30.0)
            except asyncio.TimeoutError:
                log.warning("Embedded worker did not drain within 30s; forcing")
                worker_task.cancel()
                try:
                    await worker_task
                except (asyncio.CancelledError, Exception):
                    pass
            log.info("Embedded worker stopped")
        await events_bus.stop_listener()


app = FastAPI(
    title="Class Homework Builder",
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)