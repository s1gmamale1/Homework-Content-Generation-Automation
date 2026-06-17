import asyncio

from app.api.v1.jobs import list_agent_models
from app.services.agent_models import MODEL_MANIFEST
from app.services.model_tiers import tier_of


def test_tier_of_covers_every_manifest_pair():
    for provider, models in MODEL_MANIFEST.items():
        for m in models:
            assert isinstance(tier_of(provider, m), int)


def test_endpoint_exposes_tiers():
    out = asyncio.run(list_agent_models())
    assert "tiers" in out
    prov = next(iter(MODEL_MANIFEST))
    model = MODEL_MANIFEST[prov][0]
    assert isinstance(out["tiers"][prov][model], int)
    # additive — existing keys untouched
    assert "providers" in out and "api_supported" in out
