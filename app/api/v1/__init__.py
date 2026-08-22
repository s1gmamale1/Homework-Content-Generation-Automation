from fastapi import APIRouter, Depends

from app.api.v1 import (
    batch,
    books,
    dashboard,
    health,
    jobs,
    notion,
    regeneration,
    sa_keys,
    settings,
    workers,
)
from app.auth import get_current_user, get_current_user_strict

# Health stays public (deployment liveness probes don't need a token).
# Everything else requires a router-level auth dependency. SA-key routes use
# strict header-only auth; the remaining routers preserve general header/query
# auth for SSE and source-PDF clients.
api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health.router, tags=["meta"])
api_v1_router.include_router(books.router, dependencies=[Depends(get_current_user)])
# batch BEFORE jobs: the static `/jobs/batches*` routes must be registered ahead
# of jobs' dynamic `/jobs/{job_id}`, or `/jobs/batches` is parsed as job_id="batches"
# (422). FastAPI matches in include order, so most-specific (static) wins by going first.
api_v1_router.include_router(batch.router, dependencies=[Depends(get_current_user)])
api_v1_router.include_router(jobs.router, dependencies=[Depends(get_current_user)])
api_v1_router.include_router(notion.router, dependencies=[Depends(get_current_user)])
api_v1_router.include_router(workers.router, dependencies=[Depends(get_current_user)])
api_v1_router.include_router(settings.router, dependencies=[Depends(get_current_user)])
api_v1_router.include_router(
    sa_keys.router, dependencies=[Depends(get_current_user_strict)]
)
api_v1_router.include_router(dashboard.router, dependencies=[Depends(get_current_user)])
# Regeneration is an OPERATOR workflow: general auth (the same header/query
# dependency books/batch/jobs use), never the SA-key-strict one. Mounted with
# the dependency HERE rather than relying on the router's own feature gate —
# FastAPI runs an include_router dependency before the sub-router's, so an
# anonymous request fails authentication without learning whether
# REGENERATION_ENABLED hid the routes.
api_v1_router.include_router(
    regeneration.router, dependencies=[Depends(get_current_user)]
)
