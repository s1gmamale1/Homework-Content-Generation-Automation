"""Fail-fast: every phase in the general flow has a _general prompt."""
import pathlib
import pytest
from app.services import flows
from app.services.prompts import get_prompt
from app.services.prompts import get_prompt as _gp

_PAIRS = [(s, p) for s in flows.SUPPORTED_SUBJECTS for p in flows.flow_for(s)]

_DEAD_VOCAB = [
    "options: null", "eval_mode", "min_chars", "source_concept_ids",
    "interaction_payload", "interaction_mode", "chips[]",
    "expected_reasoning_keywords", "base_damage", "accepted_variants",
    "source map", "allowed_assembly_types",
]

_ACTIVE_VOCAB_BY_PROMPT = {
    "practice-rlc.md": {"min_chars"},
}


def _assert_clean(rendered: str, *, active_vocab: set[str] | None = None):
    low = rendered.lower()
    allowed = active_vocab or set()
    hits = [
        tok for tok in _DEAD_VOCAB
        if tok not in allowed and tok.lower() in low
    ]
    assert not hits, f"dead JSON vocab still present: {hits}"


def _squash_ws(text: str) -> str:
    return " ".join(text.lower().split())


def test_cbp_has_family_token():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "case-based-preview.md").read_text(encoding="utf-8")
    assert "{{FAMILY_RULES}}" in body


def test_cbp_family_visual_defaults_distinct_and_clean():
    sci = _gp("biology", "case-based-preview")
    mat = _gp("math-algebra", "case-based-preview")
    lan = _gp("english", "case-based-preview")
    hum = _gp("history", "case-based-preview")
    for r in (sci, mat, lan, hum):
        assert "{{FAMILY_RULES}}" not in r
        _assert_clean(r)
    assert "](placeholder)" in sci and "](placeholder)" in lan and "](placeholder)" in hum
    assert sci != mat and lan != hum and sci != hum


def test_flashcards_has_family_token_and_canonical_enum():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "flashcards.md").read_text(encoding="utf-8")
    assert "{{FAMILY_RULES}}" in body
    for t in ("definition", "term_to_meaning", "process_step",
              "question_answer", "misconception", "image_label"):
        assert t in body, f"canonical type {t} missing"
    assert "FlashcardType" not in body


def test_flashcards_families_distinct_and_clean():
    sci = _gp("physics", "flashcards")
    lan = _gp("english", "flashcards")
    hum = _gp("history", "flashcards")
    mat = _gp("geometriya-g7-11", "flashcards")
    for r in (sci, lan, hum, mat):
        assert "{{FAMILY_RULES}}" not in r
        _assert_clean(r)
    assert sci != lan and lan != hum and sci != mat


def test_memory_check_clean_and_consistent():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "memory-check.md").read_text(encoding="utf-8")
    _assert_clean(_gp("biology", "memory-check"))
    assert "all 3 kinds" not in body


def test_rlc_and_error_detection_clean_with_strip_test():
    for subj, phase, path in [
        ("biology", "practice-rlc", "practice-rlc.md"),
        ("biology", "practice-error-detection", "practice-error-detection.md"),
    ]:
        body = (pathlib.Path(__file__).resolve().parents[2]
                / "prompts" / "_general" / path).read_text(encoding="utf-8")
        _assert_clean(
            _gp(subj, phase),
            active_vocab=_ACTIVE_VOCAB_BY_PROMPT.get(path),
        )
        assert "strip" in body.lower(), f"{path} missing the Strip Test rule"
    assert "**Minimum length (min_chars):**" in (
        pathlib.Path(__file__).resolve().parents[2]
        / "prompts" / "_general" / "practice-rlc.md"
    ).read_text(encoding="utf-8")


def test_boss_arena_clean_with_adaptation():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "boss-arena.md").read_text(encoding="utf-8")
    _assert_clean(_gp("biology", "boss-arena"))
    low = body.lower()
    assert "weak" in low, "missing weak-skill adaptation rule"
    for part in ("why", "how", "what"):
        assert part in low


_GAMES = [
    ("biology", "practice-memory-match"),
    ("physics", "practice-tictactoe"),
    ("geometriya-g7-11", "practice-jigsaw"),
    ("english", "practice-sentence"),
]


_MATH_ACCURACY_PROMPTS = [
    "flashcards.md",
    "memory-check.md",
    "practice-memory-match.md",
    "practice-jigsaw.md",
    "boss-arena.md",
    "practice-rlc.md",
    "practice-error-detection.md",
    "case-based-preview.md",
]


def test_math_accuracy_guardrails_cover_generation_prompts():
    gdir = pathlib.Path(__file__).resolve().parents[2] / "prompts" / "_general"
    required = [
        "expansion or substitution",
        "rational expressions",
        "theorem implications",
        "square inherits",
        "simple concave polygon",
    ]
    missing = {}
    for prompt_name in _MATH_ACCURACY_PROMPTS:
        body = _squash_ws((gdir / prompt_name).read_text(encoding="utf-8"))
        hits = [needle for needle in required if needle not in body]
        if hits:
            missing[prompt_name] = hits
    assert not missing, f"math accuracy guardrails missing: {missing}"


def test_error_detection_contract_forbids_inline_marker():
    # Task 2: the old contract instructed the generator to mark the broken
    # block inline (spoiling it for the student); the new contract forbids
    # that and pushes identification into the answer-key sections only.
    body = get_prompt("geografiya", "practice-error-detection")
    assert "(to the reader of this output, not to the student)" not in body
    assert "ONLY in" in body and "The correct version" in body


def test_flashcards_contract_scopes_coverage_to_deck_budget():
    # Task 4: the old contract demanded both exhaustive coverage (every term)
    # and a hard 6-8 card cap for G5-6, creating contradiction. Decision:
    # deck size wins; coverage is packet-level. The prompt must forbid the
    # absolutist "extract every" wording and explicitly scope to deck budget.
    body = get_prompt("geografiya", "flashcards")
    assert "Cover every term, name, structure, process, rule, and classification term" not in body
    assert "extract every key term" not in body
    assert "Deck size wins" in body
    # Hardened contract (worklog 0159): band maximum is a HARD CAP with count self-check.
    assert "HARD CAP" in body
    assert "count the cards" in body


def test_error_detection_requires_rederivation_and_feedback_consistency():
    body = _squash_ws((pathlib.Path(__file__).resolve().parents[2]
                       / "prompts" / "_general" / "practice-error-detection.md").read_text(encoding="utf-8"))
    for needle in (
        "verify every non-broken block",
        "re-derive every block",
        "feedback consistency",
        "the correct version",
        "never silently switch",
    ):
        assert needle in body, f"error-detection missing consistency guard: {needle}"


def test_math_family_rules_forbid_unverified_domain_and_geometry_claims():
    for phase in ("case-based-preview", "flashcards"):
        rendered = _gp("geometriya-g7-11", phase).lower()
        assert "unverified domain restrictions" in rendered
        assert "asserting a geometric property" in rendered
        assert "standard condition" in rendered


def test_games_clean_and_compact():
    for subj, phase in _GAMES:
        rendered = _gp(subj, phase)
        _assert_clean(rendered)
        assert "checkpoint 3" not in rendered.lower(), f"{phase} ballooned into a CBP case"


def test_reflection_instructs_top_heading_and_markdown_only():
    body = (pathlib.Path(__file__).resolve().parents[2]
            / "prompts" / "_general" / "reflection.md").read_text(encoding="utf-8")
    low = body.lower()
    assert "# " in body and ("top-level" in low or "begin your output with a single `#" in low)
    assert "markdown only" in low, "missing explicit markdown-only instruction"
    assert "omitting any section" in low, "missing all-sections gate"


@pytest.mark.parametrize("subject,phase", _PAIRS)
def test_every_flow_phase_has_a_general_prompt(subject, phase):
    body = get_prompt(subject, phase)
    assert body.strip(), f"empty prompt for {phase}"
    assert "{{SUBJECT}}" not in body  # substituted


def test_every_general_prompt_has_language_token():
    gdir = pathlib.Path(__file__).resolve().parents[2] / "prompts" / "_general"
    missing = [p.name for p in gdir.glob("*.md")
               if "{{LANGUAGE_RULES}}" not in p.read_text(encoding="utf-8")]
    assert not missing, f"prompts missing language token: {missing}"


def test_no_dead_json_vocab_anywhere_in_general_prompts():
    gdir = pathlib.Path(__file__).resolve().parents[2] / "prompts" / "_general"
    offenders = {}
    for p in gdir.glob("*.md"):
        low = p.read_text(encoding="utf-8").lower()
        allowed = _ACTIVE_VOCAB_BY_PROMPT.get(p.name, set())
        hits = [
            tok for tok in _DEAD_VOCAB
            if tok not in allowed and tok.lower() in low
        ]
        if hits:
            offenders[p.name] = hits
    assert not offenders, f"dead JSON vocab remains: {offenders}"


def test_no_unreplaced_tokens_for_any_pair():
    for subj in flows.SUPPORTED_SUBJECTS:
        for phase in flows.flow_for(subj):
            out = _gp(subj, phase)
            assert "{{" not in out, f"unreplaced token in {subj}/{phase}"
