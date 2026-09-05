"""Replay measured reviewer wording; no claim of fresh model behavior."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.services.lesson_errata import apply_lesson_errata
from scripts import smoke_homework_quality as smoke


REPLAYS = json.loads(
    (smoke.FIXTURE_ROOT / "observed-warning-replays.json").read_text(encoding="utf-8")
)
HISTORY_SECTION_ID = "768820b7-54ea-45d2-bbb4-d95275ef95e6"


def fixture(name):
    return next(f for f in smoke.load_fixtures() if f.fixture_id == name)


def outcome(warnings):
    return SimpleNamespace(available=True, refused=False, warnings=warnings, feedback="\n".join(warnings),
                           has_major=any(w.startswith("[major]") for w in warnings),
                           has_mismatch=any(w.startswith("[high]") for w in warnings))


@pytest.mark.parametrize("replay", REPLAYS, ids=lambda r: r["source_report"] + ":" + r["fixture_id"])
def test_exact_observed_warning_has_intended_relationship(replay):
    result = smoke.classify_result(fixture(replay["fixture_id"]), outcome([replay["warning"]]))
    assert result.status == replay["expected_status"]
    assert bool(result.decisive_evidence) == (replay["expected_status"] == "met")


@pytest.mark.parametrize("replay", [r for r in REPLAYS if r["expected_status"] == "met"])
def test_observed_relationship_cannot_borrow_severity_from_another_warning(replay):
    target = fixture(replay["fixture_id"])
    if target.expected_outcome not in {"major", "mismatch"}:
        return
    required = "major" if target.reviewer == "judge" else "high"
    advisory = "minor" if target.reviewer == "judge" else "medium"
    warnings = [f"[{required}] Unrelated defect in a different item",
                replay["warning"].replace(f"[{required}]", f"[{advisory}]", 1)]
    result = smoke.classify_result(target, outcome(warnings))
    assert result.status == "unmet"
    assert result.decisive_evidence == ()


def test_repaired_source_is_supplied_to_both_authoring_inputs():
    neg, pos = fixture("F01-source-negative"), fixture("F01-source-positive")
    assert neg.lesson_context == pos.lesson_context
    for text in (neg.lesson_context, pos.output_md):
        assert 'Herodotus, Histories, Book V, section 52; translated by George Rawlinson' in text
        assert '"Royal stations exist along its whole length, and excellent caravanserais;"' in text
        assert "1858" not in text
    assert "Who wrote the Sian chronicle" in neg.output_md


def test_repaired_route_card_supports_every_earlier_choice():
    neg, pos = fixture("F04-branch-negative"), fixture("F04-branch-positive")
    assert neg.lesson_context == pos.lesson_context
    for text in (neg.output_md, pos.output_md):
        assert "Which option is the starting city named in the card?" in text
        assert "Sian is a city. Pomir and Mesopotamia are regions; the Mediterranean coast is a coastal area." in text
    assert "Why did you choose this city" in neg.output_md
    assert "Whichever option you chose, use the card to name the starting city" in pos.output_md


def test_repaired_map_clause_is_answerable_and_keeps_finding_threshold():
    neg, pos = fixture("F13-map-clarity-negative"), fixture("F13-map-clarity-positive")
    assert neg.lesson_context == pos.lesson_context
    for text in (neg.output_md, pos.output_md):
        assert "3-2-ming yilliklarda" in text and "miloddan avvalgi II asrda" in text
        assert "qaysi tarixiy omillarga" not in text
    assert "Tarixiy xaritalarni tahlil qilmay" in neg.output_md
    assert neg.expected_outcome == "finding"
    assert "Yuqoridagi sanalardan foydalanib" in pos.output_md


def test_repaired_referents_share_explicit_purpose_and_action():
    neg, pos = fixture("F14-referent-negative"), fixture("F14-referent-positive")
    assert neg.lesson_context == pos.lesson_context
    for target in (neg, pos):
        assert target.output_language == "en"
        assert "open-l2-passage" in target.control_tags
        assert "The safe room protected stored goods from rain." in target.output_md
        assert "Support your answer with the passage." in target.output_md
    assert "They moved them into the safe room" in neg.output_md
    assert "The porters moved both the boxes and the bags into the safe room" in pos.output_md


def test_rabot_pair_uses_complete_reviewed_history_context_and_frozen_artifacts():
    neg = fixture("F03-history-rabot-negative")
    pos = fixture("F03-history-rabot-positive")
    canonical = apply_lesson_errata(
        "ignored stochastic extract",
        section_id=HISTORY_SECTION_ID,
        subject="history",
    )

    assert neg.lesson_context == pos.lesson_context == canonical
    for fact in (
        "Karvonsaroy",
        "$3-2$",
        "Pomir",
        "Eron",
        "Mesopotamiya",
        "Misr",
        "Doro I",
        "Baqtriya",
        "Oltoy",
        "Hindiston",
        "miloddan avvalgi II asr",
        "$12000$",
        "o‘n yetti asr",
        "Sian shahridan",
    ):
        assert fact in canonical

    archived_raw_hashes = {
        "original-history-memory-negative.md":
            "af4e24e515fa805d8aed6c73ebb55437524acbeaf3e4fc42099b3cca75cf5783",
        "original-history-memory-positive.md":
            "a77b8b07ad92859538ff712376f03d60504ba6df8a70978f03437fa3d1327394",
    }
    for name, expected_hash in archived_raw_hashes.items():
        raw = (smoke.FIXTURE_ROOT / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected_hash
        assert not raw.startswith(b"\xef\xbb\xbf")

    parent = (smoke.FIXTURE_ROOT / "original-history-memory-positive.md").read_bytes()
    derived = (
        smoke.FIXTURE_ROOT / "derived-history-memory-bojxona-positive-no-xuanxe.md"
    ).read_bytes()
    assert parent.count(b"Xitoyning Xuanxe daryosi bo'yidagi ") == 1
    assert derived == parent.replace(
        b"Xitoyning Xuanxe daryosi bo'yidagi ", b"Xitoyning ", 1
    )
    assert hashlib.sha256(derived).hexdigest() == (
        "cd3f956007bdb233ffc3331affbb158297754e01ac6dd8ab29aabc6d89a93621"
    )
    assert not derived.startswith(b"\xef\xbb\xbf")
    assert pos.output_sha256 == (
        "d05037da1c917712e9cff148745b3b20a8822d4ba8c85aaad0a26ea5ca5eb813"
    )

    # The archived artifacts retain the known flaw; the active control removes only it.
    assert "Xuanxe" not in canonical
    assert "Xuanxe" in neg.output_md
    assert "Xuanxe" not in pos.output_md
    assert "Muqobil javoblar:** Pomir tog'i" in neg.output_md
    assert "Muqobil javoblar:** Pomir tog'i" in pos.output_md


def test_repetition_positive_supplies_visible_building_functions_from_shared_context():
    neg = fixture("F09-repetition-negative")
    pos = fixture("F09-repetition-positive")

    assert neg.lesson_context == pos.lesson_context
    for fact in (
        "caravanserai gives traders a place to rest and stores goods safely",
        "mint makes coins",
        "palace is a ruler's residence",
    ):
        assert fact in pos.lesson_context.casefold()
        assert fact in pos.output_md.casefold()
    assert "Museum application" in neg.output_md
    assert "Museum preview task" in neg.prior_outputs["case-based-preview"]
    assert "Museum application" not in pos.output_md


@pytest.mark.parametrize(
    "fixture_id, unrelated_quote",
    [
        ("F07-terminology-negative", "Difficulty: unspecified"),
        ("F08-route-shape-negative", "Sian stands on the Yellow River"),
    ],
)
def test_source_contradiction_rule_cannot_credit_an_unrelated_item(
    fixture_id, unrelated_quote
):
    warning = (
        "[major] Source-fidelity (CRITICAL): raise a major failure for any factual "
        "claim about the world in the output that contradicts the lesson context — "
        + unrelated_quote
    )

    result = smoke.classify_result(fixture(fixture_id), outcome([warning]))
    assert result.status == "unmet"


@pytest.mark.parametrize("warning", [
    "[minor] Present references only — Maps are a general topic in this lesson.",
    "[minor] Present references only — The decorative placeholder should be smaller.",
    "[major] Visible evidence — qaysi tarixiy omillarga tayandingiz requires directions not supplied.",
    "[minor] Tarixiy xaritalarni tahlil qilmay — punctuation needs a comma.",
])
def test_unrelated_map_mentions_or_clarity_cannot_prove_absent_map_defect(warning):
    assert smoke.classify_result(fixture("F13-map-clarity-negative"), outcome([warning])).status == "unmet"
