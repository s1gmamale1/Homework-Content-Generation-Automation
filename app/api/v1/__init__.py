from fastapi import APIRouter, Depends

from app.api.v1 import books, health, jobs
from app.auth import get_current_user

# Health stays public (deployment liveness probes don't need a token).
# Everything else requires `Depends(get_current_user)` — attached to the
# parent router so we don't have to repeat it on every endpoint.
api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health.router, tags=["meta"])
api_v1_router.include_router(books.router, dependencies=[Depends(get_current_user)])
api_v1_router.include_router(jobs.router, dependencies=[Depends(get_current_user)])
