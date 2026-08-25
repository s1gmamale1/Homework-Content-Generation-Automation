import pytest
from app.services import prompts, subjects

NON_L2 = "matematika"  # language == "uz" (verify via registry below)
L2 = "english"         # language == "english"

def _uz_subject():
    # pick any subject whose registry language is "uz"
    for c, d in subjects.REGISTRY.items():
        if d.language == "uz":
            return c
    raise AssertionError("no uz subject in registry")

def test_uz_default_is_byte_identical_to_legacy_default():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="uz")
    assert prompts.LANGUAGE_RULES["_default"] in body
    # the en/ru medium blocks must NOT leak into a uz render
    assert prompts.MEDIUM_RULES["en"] not in body
    assert prompts.MEDIUM_RULES["ru"] not in body

def test_en_medium_injects_english_block_for_non_l2_subject():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="en")
    assert prompts.MEDIUM_RULES["en"] in body
    assert prompts.LANGUAGE_RULES["_default"] not in body

def test_ru_medium_injects_russian_block_for_non_l2_subject():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="ru")
    assert prompts.MEDIUM_RULES["ru"] in body

# Frozen copies of the pre-change L2 blocks (uz bridge). Copied verbatim from
# app/services/prompts.py @ origin/Nggaev-v2. Do NOT edit to match a code change —
# if the builder drifts, THIS is the ground truth and the test must fail.
_FROZEN_LANG_ENGLISH = (
    "This is an English (L2) lesson for native-Uzbek learners.\n"
    "CEFR ladder (per the adopted textbooks — Guess What! G5–6, Cambridge "
    "Prepare-2nd-ed G7–11 with Level 2 split across G7/G8 — and the state "
    "standard A1@G4 / A2@G9 / B1@G11): G5→A1, G6→A1+, G7→A2-early, G8→A2, "
    "G9→A2+, G10→B1, G11→B1+. If no grade is visible, infer the level "
    "from the source's own complexity (default to A2 only if truly "
    "indeterminate). The leveled CEFR governs EVERY English sentence you "
    "write — sentence length, grammar inventory, and vocabulary range — "
    "never exceed it.\n"
    "Level caps: A1 — ~800 words; sentences 3–8 words; present simple, "
    "can, there is/are, imperatives. A1+ — adds present continuous "
    "basics; ≤10 words. A2-early (G7 = Prepare 2 units 1–10) — vocabulary "
    "inside the A2 Key list (~1,500 headwords); sentences 6–12 words; "
    "present simple/continuous, adverbs of frequency, past simple, will "
    "(intro), can/can't — NOT yet past continuous or comparatives (those "
    "are G8). A2 (G8) — adds past continuous, comparatives/superlatives, "
    "going to, have to/should/may. A2+ (G9) — adds present perfect "
    "(ever/never/just/already/yet/for/since), first conditional, gerund "
    "after verbs, must/might, simple passive; reading texts to ~250 "
    "words. B1 (G10) — vocabulary inside the B1 Preliminary list (~2,900 "
    "headwords); sentences 10–18 words; adds second conditional, reported "
    "speech (intro), relative clauses, fuller passive, modals of "
    "deduction. B1+ (G11) — adds past perfect, used to, fuller reported "
    "speech and relatives; ~100-word writing tasks.\n"
    "BELOW B1 (grades 5–9): the thing being LEARNED is in English; "
    "everything that HELPS them learn it is in Uzbek (\"Siz\").\n"
    "- In English: the target vocabulary, example sentences, "
    "passages/texts, collocations, grammar items, and anything the "
    "learner must read or produce — all capped at the level.\n"
    "- In formal Uzbek (\"Siz\"): all scaffolding — task instructions, "
    "framing, hints, explanations, feedback, and the DPE/reasoning "
    "prompts (the UZ bridge).\n"
    "FROM B1 UP (grades 10–11): the ENTIRE homework is in English at the "
    "lesson's level — instructions, framing, hints, explanations, "
    "feedback, section headings, game labels, EVERYTHING. No scaffolding "
    "language and no mother-tongue text anywhere, with exactly ONE "
    "exception: the fixed translation lines the Topic Vocabulary phase "
    "itself defines. This B1+ all-English rule OVERRIDES every other "
    "language directive, including the heading-localization clause "
    "appended below this block."
)
_FROZEN_LANG_RUSSIAN = (
    "This is a Russian (L2) lesson for native-Uzbek learners.\n"
    "Governing principle: the thing being LEARNED is in Russian; everything that "
    "HELPS them learn it is in formal Uzbek (\"Siz\").\n"
    "- In Russian: the target vocabulary, example sentences, passages/texts, "
    "collocations, grammar items, and anything the learner must read or produce.\n"
    "- In formal Uzbek (\"Siz\"): all scaffolding — task instructions, framing, "
    "hints, explanations, feedback, and the DPE/reasoning prompts (the UZ bridge).\n"
    "- Level the Russian to the lesson's own complexity and the source's grade; "
    "never exceed what the source uses (no advanced constructions in an early "
    "lesson). Preserve every term, example, and form exactly as in the source; "
    "translate idiomatically into Uzbek, never word-for-word."
)


def test_l2_uz_bridge_is_byte_identical_to_frozen_legacy():
    # RED-provable: change a character in the module's builder/base → this fails.
    assert prompts._l2_rule("english", "uz") == _FROZEN_LANG_ENGLISH
    assert prompts._l2_rule("russian", "uz") == _FROZEN_LANG_RUSSIAN


def test_l2_bridge_follows_medium():
    # l2-bridge-follows-medium: the L2 TARGET stays english/russian, but the
    # scaffolding BRIDGE follows output_language.
    uz_body = prompts.get_prompt("english", "flashcards", output_language="uz")
    ru_body = prompts.get_prompt("english", "flashcards", output_language="ru")
    en_body = prompts.get_prompt("russian", "flashcards", output_language="en")

    # uz medium: the frozen uz-bridge block is present unchanged
    assert _FROZEN_LANG_ENGLISH in uz_body
    assert 'formal Uzbek ("Siz")' in uz_body

    # ru medium (english class): bridge becomes Russian, uz-bridge phrasing gone
    assert "formal Russian" in ru_body
    assert 'formal Uzbek ("Siz")' not in ru_body
    assert "Uzbek (\"Siz\")" not in ru_body   # the bare governing-line bridge too
    # still an L2 english rule, NOT the ru MEDIUM rule for non-L2 subjects
    assert prompts.MEDIUM_RULES["ru"] not in ru_body

    # en medium (russian class): bridge becomes English
    assert "formal English" in en_body
    assert 'formal Uzbek ("Siz")' not in en_body

def test_unknown_language_falls_back_to_uz():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="zz")
    assert prompts.MEDIUM_RULES["uz"] in body

def test_hash_differs_by_language():
    subj = _uz_subject()
    assert prompts.get_prompt_hash(subj, "flashcards", "uz") != \
           prompts.get_prompt_hash(subj, "flashcards", "en")


def test_ru_medium_appends_heading_localization_directive():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "case-based-preview", output_language="ru")
    assert "in the output language" in body.lower()
    low = body.lower()
    assert "heading" in low and "title" in low and "subject name" in low


def test_en_medium_appends_heading_localization_directive():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "boss-arena", output_language="en")
    assert "in the output language" in body.lower()


def test_uz_medium_has_no_heading_localization_directive():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "reflection", output_language="uz")
    assert prompts._LOCALIZE_HEADINGS_CLAUSE not in body


# --- Task 3: un-freeze uz — append a uz-language label-localization clause
# (user-approved, 2026-07-23). Append-only: the frozen #83 tests above stay green.

def test_uz_medium_gets_uz_localize_clause():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="uz")
    assert prompts._LOCALIZE_HEADINGS_CLAUSE_UZ in body
    # frozen base still present (append-only un-freeze)
    assert prompts.LANGUAGE_RULES["_default"] in body


def test_en_ru_keep_their_own_clause_not_uz():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="ru")
    assert prompts._LOCALIZE_HEADINGS_CLAUSE in body
    assert prompts._LOCALIZE_HEADINGS_CLAUSE_UZ not in body


def test_en_ru_clause_has_full_label_coverage_and_machine_key_carveout():
    """RU/EN parity with the uz clause (2026-07-24): the en/ru directive must name the
    fuller game-label set (incl. the ones only the uz clause used to carry) AND carve
    out machine-facing card keys / backtick enums, while KEEPING its en/ru-specific
    bilingual subject-name de-parenthetical instruction."""
    c = prompts._LOCALIZE_HEADINGS_CLAUSE
    assert "How to play" in c and "Relationship types" in c        # labels uz had, ru lacked
    assert "id, front, back, type, difficulty" in c                # machine-key carve-out
    assert "`easy`" in c and "`medium`" in c and "`hard`" in c      # enum-value exception
    assert "Matematika" in c                                        # kept: subject-name delocalization


def test_l2_subject_uz_medium_also_gets_uz_clause():
    """INTENTIONAL side effect: an English/Russian class packet rendered with the uz
    medium localizes its student-read labels into Uzbek too — labels are scaffolding,
    and the L2 scaffolding bridge is Uzbek."""
    body = prompts.get_prompt("english", "flashcards", output_language="uz")
    assert prompts._LOCALIZE_HEADINGS_CLAUSE_UZ in body
