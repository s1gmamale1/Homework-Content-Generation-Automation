from fastapi import APIRouter, Depends

from app.api.v1 import batch, books, health, jobs, notion, workers
from app.auth import get_current_user

# Health stays public (deployment liveness probes don't need a token).
# Everything else requires `Depends(get_current_user)` — attached to the
# parent router so we don't have to repeat it on every endpoint.
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
