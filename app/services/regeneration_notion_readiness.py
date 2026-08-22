"""Can this deployment publish to Notion at all?

One predicate, deliberately alone in a neutral module. Three callers already had
to agree on it — `main.lifespan` (don't start the publication loop), the API
routes that promise delivery (refuse before approval) and the publisher's own
`run_once` (refuse before claiming) — and the destination preflight is a fourth,
on the REVIEW side of the feature. Leaving it in `regeneration_publisher` would
have made review import publication just to ask whether Notion is configured,
which is the wrong direction and the start of an import cycle: publication is
downstream of review, not the other way round.

Nothing else belongs here. The module has no state, opens no client and touches
no database, so importing it from anywhere costs nothing and can never reorder
work that is sensitive to when it runs.
"""
from __future__ import annotations

from typing import Optional

from app.config import settings
from app.services.notion.client import normalize_api_key

__all__ = ["publication_unavailable_reason"]


def publication_unavailable_reason() -> Optional[str]:
    """Why nothing can be published on this deployment, or ``None`` when it can.

    The whole feature's fail-closed answer, in one place, so the callers that
    must agree — `main.lifespan` (don't start the loop), the two API routes
    that promise delivery (refuse before approval), `run_once` itself (refuse
    before claiming) and the destination preflight (refuse before reviewing a
    destination that cannot be published to) — cannot drift apart.

    It is deliberately about the DEPLOYMENT, not about one target: whether this
    head has a Notion destination at all. A per-lesson destination problem is a
    different thing and stays where it is, in `_prepare`, which refuses before
    reserving a version.

    Cheap and side-effect free by contract. It runs on the event loop and inside
    request handlers, so it must not build a client, open a socket or touch the
    database — `normalize_api_key` is the constructor's own credential rule with
    the client construction left out.
    """
    if not settings.notion_enabled:
        return (
            "NOTION_ENABLED is off, so this deployment has no Notion destination "
            "to publish a revision to"
        )
    try:
        normalize_api_key(settings.notion_api_key)
    except ValueError as exc:
        return f"the Notion credential is unusable: {exc}"
    return None
