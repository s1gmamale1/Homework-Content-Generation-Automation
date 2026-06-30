from pathlib import Path
from app.services.sa_key_apply import upsert_env_file


def test_upsert_preserves_non_ascii_and_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "AUTH_TOKEN=123\n"
        "NOTION_SUBJECT_PAGES=ru:Математика|5=abc\n"   # Cyrillic must survive
        "GOOGLE_CLOUD_PROJECT=old-proj\n",
        encoding="utf-8",
    )
    upsert_env_file(env, {
        "GOOGLE_APPLICATION_CREDENTIALS": "/abs/active.json",
        "GOOGLE_CLOUD_PROJECT": "new-proj",
    })
    out = env.read_text(encoding="utf-8")
    assert "NOTION_SUBJECT_PAGES=ru:Математика|5=abc" in out  # untouched, non-ASCII intact
    assert "AUTH_TOKEN=123" in out
    assert "GOOGLE_CLOUD_PROJECT=new-proj" in out and "old-proj" not in out  # replaced in place
    assert "GOOGLE_APPLICATION_CREDENTIALS=/abs/active.json" in out  # appended

    # removal: value None drops the line
    upsert_env_file(env, {"GOOGLE_APPLICATION_CREDENTIALS": None, "GOOGLE_CLOUD_PROJECT": None})
    out = env.read_text(encoding="utf-8")
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in out
    assert "GOOGLE_CLOUD_PROJECT" not in out
    assert "NOTION_SUBJECT_PAGES=ru:Математика|5=abc" in out  # still intact
