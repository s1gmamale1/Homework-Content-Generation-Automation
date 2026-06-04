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
    yield tmp_path
    P._cache.clear(); P._hash_cache.clear()


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


@pytest.fixture
def tmp_lang(tmp_path, monkeypatch):
    (tmp_path / "_general").mkdir()
    (tmp_path / "_general" / "demo.md").write_text(
        "Title for {{SUBJECT}}.\n\n{{LANGUAGE_RULES}}\n", encoding="utf-8")
    monkeypatch.setattr(P, "PROMPTS_DIR", tmp_path)
    P._cache.clear(); P._hash_cache.clear()
    yield tmp_path
    P._cache.clear(); P._hash_cache.clear()


def test_english_subject_gets_english_language_block(tmp_lang):
    out = P.get_prompt("english", "demo")
    assert "{{LANGUAGE_RULES}}" not in out
    assert "English (L2)" in out
    assert "Siz" in out
    assert "Governing principle" in out
    assert "G11→B1+" in out and "never exceed" in out


def test_nonenglish_subject_gets_uzbek_block(tmp_lang):
    out = P.get_prompt("physics", "demo")
    assert "{{LANGUAGE_RULES}}" not in out
    assert "formal Uzbek" in out
    assert "English (L2)" not in out


def test_language_token_substituted_alongside_subject(tmp_lang):
    out = P.get_prompt("physics", "demo")
    assert "{{SUBJECT}}" not in out and "{{LANGUAGE_RULES}}" not in out


@pytest.fixture
def tmp_family(tmp_path, monkeypatch):
    (tmp_path / "_general").mkdir()
    (tmp_path / "_general" / "demo.md").write_text(
        "Title for {{SUBJECT}}.\n\n{{FAMILY_RULES}}\n", encoding="utf-8")
    (tmp_path / "_general" / "noblocks.md").write_text(
        "No family here for {{SUBJECT}}.\n\n{{FAMILY_RULES}}\n", encoding="utf-8")
    monkeypatch.setattr(P, "PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(P, "_SUBJECT_FAMILY", {
        "biology": "sciences", "math-algebra": "math",
        "english": "languages", "history": "humanities",
    })
    monkeypatch.setattr(P, "FAMILY_RULES", {"demo": {
        "sciences": "SCI-BLOCK", "math": "MATH-BLOCK",
        "languages": "LANG-BLOCK", "humanities": "HUM-BLOCK",
        "_default": "DEFAULT-BLOCK",
    }})
    P._cache.clear(); P._hash_cache.clear()
    yield tmp_path
    P._cache.clear(); P._hash_cache.clear()


def test_family_token_resolves_per_subject(tmp_family):
    assert "SCI-BLOCK" in P.get_prompt("biology", "demo")
    assert "MATH-BLOCK" in P.get_prompt("math-algebra", "demo")
    assert "LANG-BLOCK" in P.get_prompt("english", "demo")
    assert "HUM-BLOCK" in P.get_prompt("history", "demo")
    assert "{{FAMILY_RULES}}" not in P.get_prompt("biology", "demo")


def test_family_unmapped_subject_falls_to_phase_default(tmp_family):
    out = P.get_prompt("kimyo-g7-11", "demo")
    assert "DEFAULT-BLOCK" in out
    for leaked in ("SCI-BLOCK", "MATH-BLOCK", "LANG-BLOCK", "HUM-BLOCK"):
        assert leaked not in out


def test_family_no_entry_for_phase_collapses_to_empty(tmp_family):
    out = P.get_prompt("biology", "noblocks")
    assert "{{FAMILY_RULES}}" not in out
    for leaked in ("SCI-BLOCK", "DEFAULT-BLOCK"):
        assert leaked not in out


def test_family_missing_block_for_family_falls_to_default(tmp_family, monkeypatch):
    monkeypatch.setattr(P, "FAMILY_RULES", {"demo": {
        "sciences": "SCI-BLOCK", "_default": "DEFAULT-BLOCK"}})
    P._cache.clear(); P._hash_cache.clear()
    out = P.get_prompt("math-algebra", "demo")
    assert "DEFAULT-BLOCK" in out and "SCI-BLOCK" not in out
