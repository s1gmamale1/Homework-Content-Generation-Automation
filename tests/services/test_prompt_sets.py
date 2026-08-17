"""Repository-backed prompt-set registry (PR A / Task 1, selective-regeneration).

Pure unit tests: no DB, no network, no API credentials. `homework-v1` is the
frozen legacy set -- moving `prompts/_general/` to `prompts/sets/homework-v1/
_general/` must not change a single resolved byte. The golden fixture
(`tests/fixtures/prompt_sets/homework-v1-resolved-sha256.json`) was captured
from the PRE-MOVE tree specifically so this parity claim has independent
evidence -- comparing the default path against the explicit v1 path alone
would only prove the two call sites agree with each other, not that either
still matches what shipped before.
"""
import hashlib
import json
from pathlib import Path

import pytest

from app.services import flows
from app.services import prompt_sets as PS
from app.services.prompt_sets import (
    LEGACY_PROMPT_SET_ID,
    PromptSetSpec,
    get_prompt_set,
    list_prompt_sets,
)
from app.services.prompts import (
    PROMPTS_DIR,
    get_prompt,
    get_prompt_hash,
    get_structured_prompt,
    get_teacher_deck_fidelity_contract,
)

_GOLDEN_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests" / "fixtures" / "prompt_sets" / "homework-v1-resolved-sha256.json"
)
_LANGUAGES = ["uz", "en", "ru"]
_STRUCTURED_PHASES = ["practice-rlc", "practice-sentence", "teacher-deck"]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Registration -----------------------------------------------------------

def test_legacy_prompt_set_is_registered_and_resolves_current_tree():
    sets = {s.id: s for s in list_prompt_sets()}
    assert LEGACY_PROMPT_SET_ID in sets
    assert sets["homework-v1"].root == PROMPTS_DIR / "sets" / "homework-v1"
    assert get_prompt("history", "flashcards", prompt_set_id="homework-v1") == \
           get_prompt("history", "flashcards")


def test_get_prompt_set_returns_the_same_spec():
    spec = get_prompt_set(LEGACY_PROMPT_SET_ID)
    assert isinstance(spec, PromptSetSpec)
    assert spec.id == LEGACY_PROMPT_SET_ID
    assert spec.root == PROMPTS_DIR / "sets" / "homework-v1"
    assert spec.label
    assert spec.description


def test_unknown_prompt_set_fails_before_any_model_call():
    with pytest.raises(KeyError, match="unknown prompt set"):
        get_prompt("history", "flashcards", prompt_set_id="missing-v9")


def test_default_prompt_set_id_is_the_legacy_id():
    assert get_prompt("history", "flashcards") == \
           get_prompt("history", "flashcards", prompt_set_id=LEGACY_PROMPT_SET_ID)


# --- Golden parity: pre-move fixture vs. post-move homework-v1 --------------

def test_golden_fixture_exists_and_is_nonempty():
    assert _GOLDEN_PATH.is_file(), "golden parity fixture missing -- see task-1 brief Step 1"
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert golden["get_prompt"]
    assert golden["get_structured_prompt"]
    assert golden["teacher_deck_fidelity"]


def test_homework_v1_get_prompt_matches_pregolden_fixture():
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    mismatches = []
    for key, want in golden["get_prompt"].items():
        subject, phase, lang = key.split("|")
        got = _sha(get_prompt(subject, phase, output_language=lang,
                               prompt_set_id=LEGACY_PROMPT_SET_ID))
        if got != want:
            mismatches.append(key)
    assert not mismatches, f"homework-v1 drifted from pre-move golden: {mismatches[:10]}"


def test_homework_v1_get_structured_prompt_matches_pregolden_fixture():
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    mismatches = []
    for key, want in golden["get_structured_prompt"].items():
        subject, phase, lang = key.split("|")
        body = get_structured_prompt(subject, phase, output_language=lang,
                                      prompt_set_id=LEGACY_PROMPT_SET_ID)
        got = _sha(body) if body is not None else None
        if got != want:
            mismatches.append(key)
    assert not mismatches, f"homework-v1 structured drift from pre-move golden: {mismatches[:10]}"


def test_homework_v1_teacher_deck_fidelity_matches_pregolden_fixture():
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    got = _sha(get_teacher_deck_fidelity_contract(prompt_set_id=LEGACY_PROMPT_SET_ID))
    assert got == golden["teacher_deck_fidelity"]


def test_default_path_and_explicit_v1_path_agree_for_every_pair():
    # Belt-and-suspenders on top of the golden comparison above: the two call
    # shapes (omitted kwarg vs. explicit legacy id) must never diverge.
    for subject in flows.SUPPORTED_SUBJECTS:
        for phase in flows.flow_for(subject):
            for lang in _LANGUAGES:
                assert get_prompt(subject, phase, output_language=lang) == \
                       get_prompt(subject, phase, output_language=lang,
                                  prompt_set_id=LEGACY_PROMPT_SET_ID)


def test_get_prompt_hash_matches_resolved_output_for_named_set():
    body = get_prompt("history", "flashcards", output_language="uz",
                       prompt_set_id=LEGACY_PROMPT_SET_ID)
    assert get_prompt_hash("history", "flashcards", "uz",
                            prompt_set_id=LEGACY_PROMPT_SET_ID) == _sha(body)


# --- Manifest validation (temporary test prompt root/manifest) --------------

def _write_minimal_prompt_root(root: Path) -> None:
    general = root / "_general"
    structured = general / "structured"
    structured.mkdir(parents=True)
    for name in PS.REQUIRED_PHASE_FILES:
        (general / name).write_text(
            f"# {name}\n\n{{{{SUBJECT}}}}\n\n{{{{LANGUAGE_RULES}}}}\n\n{{{{FAMILY_RULES}}}}\n",
            encoding="utf-8",
        )
    for rel in PS.REQUIRED_STRUCTURED_FILES:
        (general / rel).write_text(f"STRUCTURED {rel} for {{{{SUBJECT}}}}\nJSON only.\n",
                                    encoding="utf-8")
    for rel in PS.REQUIRED_FIDELITY_FILES:
        (general / rel).write_text(f"FIDELITY {rel}\n", encoding="utf-8")


@pytest.fixture
def tmp_registry(tmp_path, monkeypatch):
    """A throwaway second prompt-set tree + manifest, isolated from the real
    registry cache so registry-shape tests never touch prompts/prompt-sets.json."""
    _write_minimal_prompt_root(tmp_path / "sets" / "test-v1")
    manifest = {
        "schema": "hcga-prompt-sets@1",
        "default": "test-v1",
        "sets": [
            {
                "id": "test-v1",
                "label": "Test set",
                "root": "sets/test-v1",
                "description": "throwaway test set",
            }
        ],
    }
    manifest_path = tmp_path / "prompt-sets.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(PS, "MANIFEST_PATH", manifest_path)
    PS._reset_cache_for_tests()
    yield tmp_path, manifest
    PS._reset_cache_for_tests()


def test_manifest_drives_registration(tmp_registry):
    _tmp_path, _manifest = tmp_registry
    sets = {s.id: s for s in list_prompt_sets()}
    assert set(sets) == {"test-v1"}
    assert sets["test-v1"].label == "Test set"


def test_manifest_rejects_invalid_id(tmp_registry):
    tmp_path, manifest = tmp_registry
    manifest["sets"][0]["id"] = "Bad_ID!"
    (tmp_path / "prompt-sets.json").write_text(json.dumps(manifest), encoding="utf-8")
    PS._reset_cache_for_tests()
    with pytest.raises(PS.PromptSetManifestError):
        list_prompt_sets()


def test_manifest_rejects_duplicate_ids(tmp_registry):
    tmp_path, manifest = tmp_registry
    dup = dict(manifest["sets"][0])
    manifest["sets"].append(dup)
    (tmp_path / "prompt-sets.json").write_text(json.dumps(manifest), encoding="utf-8")
    PS._reset_cache_for_tests()
    with pytest.raises(PS.PromptSetManifestError, match="duplicate"):
        list_prompt_sets()


def test_manifest_rejects_root_outside_prompts_dir(tmp_registry):
    tmp_path, manifest = tmp_registry
    manifest["sets"][0]["root"] = "../escaped"
    (tmp_path / "prompt-sets.json").write_text(json.dumps(manifest), encoding="utf-8")
    PS._reset_cache_for_tests()
    with pytest.raises(PS.PromptSetManifestError, match="escapes"):
        list_prompt_sets()


def test_manifest_rejects_missing_required_contract_file(tmp_registry):
    tmp_path, manifest = tmp_registry
    (tmp_path / "sets" / "test-v1" / "_general" / "boss-arena.md").unlink()
    PS._reset_cache_for_tests()
    with pytest.raises(PS.PromptSetManifestError, match="missing required contract files"):
        list_prompt_sets()


def test_manifest_rejects_unknown_default(tmp_registry):
    tmp_path, manifest = tmp_registry
    manifest["default"] = "does-not-exist"
    (tmp_path / "prompt-sets.json").write_text(json.dumps(manifest), encoding="utf-8")
    PS._reset_cache_for_tests()
    with pytest.raises(PS.PromptSetManifestError, match="default"):
        list_prompt_sets()


def test_get_prompt_set_unknown_id_raises_keyerror(tmp_registry):
    with pytest.raises(KeyError, match="unknown prompt set"):
        get_prompt_set("nope")
