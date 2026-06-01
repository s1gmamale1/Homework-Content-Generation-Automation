import pytest
from app.services import prompts as P


@pytest.fixture
def tmp_prompts(tmp_path, monkeypatch):
    (tmp_path / "_general").mkdir()
    (tmp_path / "_general" / "boss-arena.md").write_text(
        "Boss for {{SUBJECT}} only.", encoding="utf-8")
    (tmp_path / "physics").mkdir()
    (tmp_path / "physics" / "boss-arena.md").write_text(
        "SUBJECT-SPECIFIC physics boss.", encoding="utf-8")
    monkeypatch.setattr(P, "PROMPTS_DIR", tmp_path)
    P._cache.clear(); P._hash_cache.clear()
    return tmp_path


def test_general_only_and_subject_substitution(tmp_prompts):
    out = P.get_prompt("physics", "boss-arena")
    assert "{{SUBJECT}}" not in out
    assert "Boss for" in out and "Physics" in out
    assert "SUBJECT-SPECIFIC" not in out  # subject file ignored in MVP


def test_provider_suffix_preserved(tmp_prompts):
    out = P.get_prompt("physics", "boss-arena", provider_suffix="USE $imagegen")
    assert out.endswith("USE $imagegen")


def test_switch_prefers_subject_when_enabled(tmp_prompts, monkeypatch):
    monkeypatch.setattr(P, "USE_SUBJECT_PROMPTS", True)
    P._cache.clear(); P._hash_cache.clear()
    out = P.get_prompt("physics", "boss-arena")
    assert "SUBJECT-SPECIFIC" in out
