"""Tests for app/services/prompts.py — prompt loading and caching."""
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

import app.services.prompts as prompts_module
from app.services.prompts import _load_subject, get_prompt, get_prompt_hash, load_all


@pytest.fixture(autouse=True)
def clear_caches():
    """Reset the module-level caches between tests."""
    prompts_module._cache.clear()
    prompts_module._hash_cache.clear()
    yield
    prompts_module._cache.clear()
    prompts_module._hash_cache.clear()


class TestLoadSubject:
    def test_raises_for_missing_directory(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Prompt directory not found"):
            _load_subject.__wrapped__(tmp_path / "nonexistent") if hasattr(
                _load_subject, "__wrapped__"
            ) else _load_subject("nonexistent-subject-xyz")

    def test_loads_md_files(self, tmp_path, monkeypatch):
        subject_dir = tmp_path / "test-subject"
        subject_dir.mkdir()
        (subject_dir / "flashcards.md").write_text("## Flashcards prompt", encoding="utf-8")
        (subject_dir / "preview-hard.md").write_text("## Preview prompt", encoding="utf-8")

        monkeypatch.setattr(prompts_module, "PROMPTS_DIR", tmp_path)
        bodies, hashes = _load_subject("test-subject")

        assert "flashcards" in bodies
        assert "preview-hard" in bodies
        assert bodies["flashcards"] == "## Flashcards prompt"

    def test_computes_sha256_hashes(self, tmp_path, monkeypatch):
        subject_dir = tmp_path / "test-subject"
        subject_dir.mkdir()
        content = "Some prompt content"
        (subject_dir / "preview.md").write_text(content, encoding="utf-8")

        monkeypatch.setattr(prompts_module, "PROMPTS_DIR", tmp_path)
        _, hashes = _load_subject("test-subject")

        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert hashes["preview"] == expected

    def test_empty_directory_returns_empty_dicts(self, tmp_path, monkeypatch):
        subject_dir = tmp_path / "test-subject"
        subject_dir.mkdir()

        monkeypatch.setattr(prompts_module, "PROMPTS_DIR", tmp_path)
        bodies, hashes = _load_subject("test-subject")

        assert bodies == {}
        assert hashes == {}

    def test_non_md_files_ignored(self, tmp_path, monkeypatch):
        subject_dir = tmp_path / "test-subject"
        subject_dir.mkdir()
        (subject_dir / "flow.md").write_text("flow", encoding="utf-8")
        (subject_dir / "notes.txt").write_text("should be ignored", encoding="utf-8")

        monkeypatch.setattr(prompts_module, "PROMPTS_DIR", tmp_path)
        bodies, _ = _load_subject("test-subject")

        assert "flow" in bodies
        assert "notes" not in bodies


class TestGetPrompt:
    def test_returns_prompt_from_real_prompt_dir(self):
        # Uses the actual prompts/ directory in the repo.
        # Pick a phase that's defined for all supported subjects.
        from app.services.flows import SUPPORTED_SUBJECTS
        subject = SUPPORTED_SUBJECTS[0]
        # load_all() must be called to warm the cache
        load_all()
        # Every subject should have at least one prompt
        from app.services.flows import SUBJECT_FLOWS
        first_phase = SUBJECT_FLOWS[subject]["hard"][0]
        result = get_prompt(subject, first_phase)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_raises_keyerror_for_missing_phase(self):
        from app.services.flows import SUPPORTED_SUBJECTS
        subject = SUPPORTED_SUBJECTS[0]
        load_all()
        with pytest.raises(KeyError, match="Prompt .* not found"):
            get_prompt(subject, "nonexistent-phase-xyz")

    def test_lazy_loads_subject_on_first_access(self, tmp_path, monkeypatch):
        subject_dir = tmp_path / "lazy-subject"
        subject_dir.mkdir()
        (subject_dir / "flashcards.md").write_text("Lazy loaded content", encoding="utf-8")

        monkeypatch.setattr(prompts_module, "PROMPTS_DIR", tmp_path)
        # Cache is empty — get_prompt should trigger _load_subject
        result = get_prompt("lazy-subject", "flashcards")
        assert result == "Lazy loaded content"

    def test_caches_after_first_access(self, tmp_path, monkeypatch):
        subject_dir = tmp_path / "cached-subject"
        subject_dir.mkdir()
        (subject_dir / "phase.md").write_text("cached", encoding="utf-8")

        monkeypatch.setattr(prompts_module, "PROMPTS_DIR", tmp_path)
        get_prompt("cached-subject", "phase")
        # Remove file — second call should use cache
        (subject_dir / "phase.md").unlink()
        result = get_prompt("cached-subject", "phase")
        assert result == "cached"


class TestGetPromptHash:
    def test_hash_matches_sha256_of_prompt_content(self, tmp_path, monkeypatch):
        subject_dir = tmp_path / "hash-subject"
        subject_dir.mkdir()
        content = "Prompt content for hashing"
        (subject_dir / "classify.md").write_text(content, encoding="utf-8")

        monkeypatch.setattr(prompts_module, "PROMPTS_DIR", tmp_path)
        h = get_prompt_hash("hash-subject", "classify")

        assert h == hashlib.sha256(content.encode("utf-8")).hexdigest()

    def test_hash_is_stable_for_same_content(self, tmp_path, monkeypatch):
        subject_dir = tmp_path / "stable-subject"
        subject_dir.mkdir()
        (subject_dir / "preview.md").write_text("stable content", encoding="utf-8")

        monkeypatch.setattr(prompts_module, "PROMPTS_DIR", tmp_path)
        h1 = get_prompt_hash("stable-subject", "preview")
        h2 = get_prompt_hash("stable-subject", "preview")
        assert h1 == h2

    def test_different_content_different_hash(self, tmp_path, monkeypatch):
        subject_dir = tmp_path / "diff-subject"
        subject_dir.mkdir()
        (subject_dir / "a.md").write_text("content A", encoding="utf-8")
        (subject_dir / "b.md").write_text("content B", encoding="utf-8")

        monkeypatch.setattr(prompts_module, "PROMPTS_DIR", tmp_path)
        assert get_prompt_hash("diff-subject", "a") != get_prompt_hash("diff-subject", "b")


class TestLoadAll:
    def test_populates_cache_for_all_subjects(self):
        from app.services.flows import SUPPORTED_SUBJECTS
        load_all()
        for subject in SUPPORTED_SUBJECTS:
            assert subject in prompts_module._cache
            assert subject in prompts_module._hash_cache

    def test_cache_values_are_dicts(self):
        from app.services.flows import SUPPORTED_SUBJECTS
        load_all()
        for subject in SUPPORTED_SUBJECTS:
            assert isinstance(prompts_module._cache[subject], dict)
            assert isinstance(prompts_module._hash_cache[subject], dict)
