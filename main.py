from fastapi import FastAPI

from app.api.v1 import api_v1_router
from app.config import settings

app = FastAPI(
    title="Edu-Homework",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
)

app.include_router(api_v1_router)


@app.get("/health")
async def root_health() -> dict:
    return {"status": "ok"}
