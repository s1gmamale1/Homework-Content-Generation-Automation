from fastapi import APIRouter

from app.api.v1 import books, health, jobs

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health.router, tags=["meta"])
api_v1_router.include_router(books.router)
api_v1_router.include_router(jobs.router)
