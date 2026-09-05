"""Resolver wiring checks; these do not claim a model follows the policy."""

import hashlib
import re
import shutil

import pytest

from app.services import flows, prompts, subjects


POLICY_HEADING = "## Shared learner-quality policy"
LEARNER_PHASES = (
    "case-based-preview", "flashcards", "memory-check", "practice-rlc",
    "practice-error-detection", "practice-sentence",
)


@pytest.fixture(autouse=True)
def isolated_prompt_cache(monkeypatch):
    monkeypatch.setattr(prompts, "_cache", {})
    monkeypatch.setattr(prompts, "_hash_cache", {})


@pytest.mark.parametrize("subject", subjects.REGISTRY)
@pytest.mark.parametrize("language", ("uz", "ru", "en"))
def test_every_active_learner_contract_receives_policy_once(subject, language):
    phases = [p for p in flows.flow_for(subject) if p != "teacher-pack"]
    assert set(LEARNER_PHASES) <= set(phases)
    for phase in phases:
        body = prompts.get_prompt(subject, phase, "PROVIDER SENTINEL", language)
        assert body.count(POLICY_HEADING) == 1, phase
        policy = (prompts.PROMPTS_DIR / "_general" / "_learner-quality.md").read_text(
            encoding="utf-8"
        ).strip()
        assert body.count(policy) == 1, phase
        assert body.endswith("\n\nPROVIDER SENTINEL"), phase
        assert not re.search(r"\{\{[A-Z_]+\}\}", body), phase
        assert prompts.get_prompt_hash(subject, phase, language) == hashlib.sha256(
            prompts.get_prompt(subject, phase, output_language=language).encode()
        ).hexdigest()


@pytest.mark.parametrize("subject", subjects.REGISTRY)
@pytest.mark.parametrize("language", ("uz", "ru", "en"))
def test_structured_learner_contracts_receive_same_policy_once(subject, language):
    for phase in ("practice-rlc", "practice-sentence"):
        body = prompts.get_structured_prompt(subject, phase, output_language=language)
        assert body.count(POLICY_HEADING) == 1, phase
        policy = (prompts.PROMPTS_DIR / "_general" / "_learner-quality.md").read_text(
            encoding="utf-8"
        ).strip()
        assert body.count(policy) == 1, phase
        assert "JSON only" in body
        assert "Markdown only" not in body
        assert not re.search(r"\{\{[A-Z_]+\}\}", body), phase


@pytest.mark.parametrize("language", ("uz", "ru", "en"))
def test_teacher_and_dormant_contracts_stay_outside_policy(language):
    for subject in subjects.REGISTRY:
        for phase in ("teacher-pack", "boss-arena", "reflection", "practice-jigsaw",
                      "practice-memory-match", "practice-tictactoe"):
            assert POLICY_HEADING not in prompts.get_prompt(
                subject, phase, output_language=language
            )
        assert POLICY_HEADING not in prompts.get_structured_prompt(
            subject, "teacher-deck", output_language=language
        )


def test_shared_policy_update_changes_resolved_content_and_hashes(tmp_path, monkeypatch):
    shutil.copytree(prompts.PROMPTS_DIR / "_general", tmp_path / "_general")
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    before = {p: prompts.get_prompt_hash("history", p) for p in LEARNER_PHASES}
    teacher_before = prompts.get_prompt_hash("history", "teacher-pack")
    policy = tmp_path / "_general" / "_learner-quality.md"
    # Also creates the missing policy on the pre-fix resolver: the failure must
    # demonstrate that resolver output ignores the policy, not a missing file.
    old = policy.read_text(encoding="utf-8") if policy.exists() else ""
    policy.write_text(old + "\nUNIQUE POLICY REVISION\n", encoding="utf-8")
    prompts.load_all()
    for phase in LEARNER_PHASES:
        assert prompts.get_prompt_hash("history", phase) != before[phase]
        assert prompts.get_prompt("history", phase).count("UNIQUE POLICY REVISION") == 1
    for phase in ("practice-rlc", "practice-sentence"):
        assert prompts.get_structured_prompt("history", phase).count("UNIQUE POLICY REVISION") == 1
    assert prompts.get_prompt_hash("history", "teacher-pack") == teacher_before


def test_subject_override_cannot_bypass_shared_policy(tmp_path, monkeypatch):
    (tmp_path / "_general").mkdir()
    (tmp_path / "_general" / "_learner-quality.md").write_text(
        POLICY_HEADING + "\nPOLICY SENTINEL", encoding="utf-8"
    )
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "practice-rlc.md").write_text(
        "Subject override for {{SUBJECT}}", encoding="utf-8"
    )
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(prompts, "USE_SUBJECT_PROMPTS", True)
    body = prompts.get_prompt("history", "practice-rlc")
    assert "Subject override" in body
    assert body.count("POLICY SENTINEL") == 1


@pytest.mark.parametrize("subject", ("math-algebra", "history"))
def test_learner_notation_defers_typed_syntax_to_phase_contract(subject):
    body = prompts.get_prompt(subject, "practice-error-detection")
    assert "keyboard-serialized LaTeX in error detection" in body
    assert "no $, no backslash commands" not in body
    assert "no $ and no backslash commands" not in body
    # Teacher contracts keep their existing notation policy.
    assert "keyboard-serialized LaTeX in error detection" not in prompts.get_prompt(
        subject, "teacher-pack"
    )


@pytest.mark.parametrize("language", ("uz", "ru", "en"))
def test_memory_diversity_respects_narrow_lesson_item_count(language):
    # This checks the resolved authoring contract, not model compliance. A
    # one/two-target lesson must not need extra facts to satisfy diversity.
    body = " ".join(prompts.get_prompt(
        "history", "memory-check", output_language=language
    ).split())
    assert "one lettered item has no letter-diversity requirement" in body
    assert "two lettered items use two different correct letters" in body
    assert "three or more lettered items use at least three different correct letters" in body
    assert "One item may use one kind" in body
    assert "two or more items, use at least two suitable kinds when the distinct targets support them" in body
    assert "60% per kind only when the item count and suitable kinds make that feasible" in body
    assert "Never add items or repeat facts to meet diversity" in body
    # Catch stale unconditional copies in the format/rules/self-check sections.
    assert "Use at least 2 of the 3 kinds. No more than 60%" not in body
    assert "At least 2 of the 3 kinds represented? No kind exceeds ~60%?" not in body
    assert "keep the kinds balanced (no more than ~60% one kind)" not in body
