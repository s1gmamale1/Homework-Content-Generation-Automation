"""Read-only dashboard-viewer app — a second FastAPI process.

Serves ONLY the coverage dashboard: its own `/health`, the dashboard router
(`/api/v1/dashboard/coverage`), and (when built) the `web/dist-viewer` SPA
bundle. No worker, no lifespan sweeps, no operator routes, no docs surface.

Auth is `get_viewer_user` (header-only Bearer, `DASHBOARD_TOKEN` only) —
deliberately separate from the operator app's `AUTH_TOKEN`. Startup refuses
outright (see `lifespan` below) rather than ever serving this port wide-open
or with a token that also grants operator access.

Run: `uv run uvicorn viewer_main:app --host 0.0.0.0 --port 8001`
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger as log

from app.api.v1 import dashboard
from app.auth import get_viewer_user
from app.config import valid_auth_tokens, valid_dashboard_tokens
from app.db import engine
from app.log import configure as configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail loud rather than ever serving this port wide-open: an unconfigured
    # DASHBOARD_TOKEN, on the operator app, means "auth disabled" for a
    # trusted dev setup — but this port's whole point is handing the URL out,
    # so an empty token here must refuse to start, not fall open.
    dashboard_tokens = valid_dashboard_tokens()
    if not dashboard_tokens:
        raise RuntimeError(
            "viewer refuses to start: DASHBOARD_TOKEN is unconfigured. "
            "Set DASHBOARD_TOKEN (comma-separated for multiple viewer "
            "tokens) before starting viewer_main."
        )

    # A token valid on BOTH ports would defeat the entire point of keeping
    # separate secrets (revocable, non-escalating) — refuse that too.
    overlap = dashboard_tokens & valid_auth_tokens()
    if overlap:
        raise RuntimeError(
            "viewer refuses to start: DASHBOARD_TOKEN overlaps AUTH_TOKEN "
            "(a shared secret would let an operator token authenticate on "
            "the viewer port, defeating token separation). Use disjoint "
            "token sets for AUTH_TOKEN and DASHBOARD_TOKEN."
        )

    log.info("Viewer app starting (read-only: health + coverage only)")
    try:
        yield
    finally:
        await engine.dispose()
        log.info("Viewer app stopped")


app = FastAPI(
    title="Class Homework Builder — Viewer",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# Same router object the operator app uses (app/api/v1/__init__.py composes
# it under api_v1_router with prefix="/api/v1" + get_current_user; here we
# include it directly under the SAME prefix but gated by get_viewer_user
# instead — same path (/api/v1/dashboard/coverage), different auth).
app.include_router(
    dashboard.router,
    prefix="/api/v1",
    dependencies=[Depends(get_viewer_user)],
)


@app.get("/health")
async def viewer_health() -> dict:
    # Deliberately static — never the operator health router, which can
    # surface DB-error detail. This port's health check reveals nothing.
    return {"ok": True}


# ─── SPA mount ─────────────────────────────────────────────────────────
# Mirrors main.py's SPA-mount idiom (main.py:167-196) but serves the
# dashboard-only build (web/dist-viewer) instead of the full operator SPA.
# Conditional so a viewer checkout without a viewer FE build doesn't fail
# to start.

WEB_DIST = Path(__file__).resolve().parent / "web" / "dist-viewer"

if WEB_DIST.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=WEB_DIST / "assets"),
        name="viewer-spa-assets",
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

    from app.config import settings

    uvicorn.run(app, host=settings.host, port=8001)
