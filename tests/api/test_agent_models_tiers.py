import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.api.v1.jobs import list_agent_models
from app.services.agent_models import MODEL_MANIFEST
from app.services.model_tiers import tier_of


def test_tier_of_covers_every_manifest_pair():
    for provider, models in MODEL_MANIFEST.items():
        for m in models:
            assert isinstance(tier_of(provider, m), int)


def _mock_session_no_workers() -> AsyncMock:
    """Minimal AsyncSession mock: await session.execute(...) returns a sync result
    object whose .scalars().all() returns an empty list (zero online workers)."""
    mock = AsyncMock()
    # execute is async → return_value is what `await session.execute(...)` resolves to.
    # scalars() and all() are synchronous calls on that result, so use MagicMock.
    sync_result = MagicMock()
    sync_result.scalars.return_value.all.return_value = []
    mock.execute.return_value = sync_result
    return mock


def test_endpoint_exposes_tiers():
    out = asyncio.run(list_agent_models(session=_mock_session_no_workers()))
    assert "tiers" in out
    prov = next(iter(MODEL_MANIFEST))
    model = MODEL_MANIFEST[prov][0]
    assert isinstance(out["tiers"][prov][model], int)
    # additive — existing keys untouched
    assert "providers" in out and "api_supported" in out
