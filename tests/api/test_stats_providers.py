def test_stats_providers_derived_from_registry_includes_opencode():
    from app.api.v1.jobs import _STATS_PROVIDERS
    from app.services.providers import PROVIDERS
    assert set(_STATS_PROVIDERS) == set(PROVIDERS), \
        "stats providers must match the live provider registry (incl. opencode)"
    assert "opencode" in _STATS_PROVIDERS
