from app.config import Settings


def test_notion_defaults_disabled(monkeypatch):
    # Assert the CODE defaults, not the dev .env. Without this isolation the
    # test reads the local .env (NOTION_ENABLED=true) and the dev environment,
    # so disable env-file loading and clear any ambient NOTION_* overrides.
    for var in ("NOTION_ENABLED", "NOTION_API_KEY", "NOTION_SUBJECT_PAGES"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y")
    assert s.notion_enabled is False
    assert s.notion_api_key == ""
    assert s.notion_subject_pages == {}


def test_notion_subject_pages_parses_json_env(monkeypatch):
    monkeypatch.setenv("NOTION_ENABLED", "true")
    monkeypatch.setenv("NOTION_API_KEY", "ntn_test")
    monkeypatch.setenv(
        "NOTION_SUBJECT_PAGES", '{"geometriya-g7-11|8": "2c4998381c7680a099fcfa8277758da9"}'
    )
    s = Settings(database_url="postgresql+asyncpg://x/y")
    assert s.notion_enabled is True
    assert s.notion_api_key == "ntn_test"
    assert s.notion_subject_pages["geometriya-g7-11|8"] == "2c4998381c7680a099fcfa8277758da9"
