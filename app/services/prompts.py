import hashlib
from pathlib import Path

from app.services import prompt_sets, subjects
from app.services.prompt_sets import LEGACY_PROMPT_SET_ID

PROMPTS_DIR = prompt_sets.PROMPTS_DIR
GENERAL_DIR = "_general"
# MVP: general prompts serve every subject. Set True later to prefer a
# subject-specific prompt when prompts/<subject>/<phase>.md exists.
USE_SUBJECT_PROMPTS = False

# Derived from the single source of truth (app/services/subjects.py).
SUBJECT_LABELS = {c: d.label for c, d in subjects.REGISTRY.items()}

_LANG_UZBEK = (
    "All student-facing text in natural, formal Uzbek. Formal \"Siz\" register "
    "throughout: never \"sen\"/\"san\", never mix \"Siz\" with informal verb forms, "
    "and avoid casual progressive endings (\"-yapti\"/\"-iyapti\") — keep progressive "
    "forms consistent within a paragraph. "
    "Simplify the WORDING around the subject, not the subject itself: never change "
    "any formula, number, unit, date, fact, or answer logic to make text easier. "
    "Preserve every term, formula, number, unit, and symbol exactly as in the "
    "source; for a difficult term, keep it and add a short plain-language gloss "
    "rather than deleting it. "
    "Write natural Uzbek — avoid Russian/English calque phrasing. "
    "Split long sentences at logical points, but avoid robotic sentence-chopping "
    "and avoid childish or slang style. Modern professional (non-bazaar) contexts. "
    "Do not globally normalize apostrophes, but never mix apostrophe styles within "
    "one homework — pick one and stay consistent."
)

_LANG_ENGLISH = (
    "This is an English (L2) lesson for native-Uzbek learners.\n"
    "Governing principle: the thing being LEARNED is in English; everything that "
    "HELPS them learn it is in Uzbek (\"Siz\").\n"
    "- In English: the target vocabulary, example sentences, passages/texts, "
    "collocations, grammar items, and anything the learner must read or produce.\n"
    "- In formal Uzbek (\"Siz\"): all scaffolding — task instructions, framing, "
    "hints, explanations, feedback, and the DPE/reasoning prompts (the UZ bridge).\n"
    "- CEFR (A1–B1+): if the source shows a grade, level the English via "
    "G5→A1, G6→A1+, G7→A2, G8→A2, G9→A2+, G10→B1, G11→B1+ (the Uzbek national "
    "curriculum keeps A2 across the G5–9 band; B1 only after G9); otherwise infer "
    "the level from the source's own complexity (default to A2 if truly "
    "indeterminate). CEFR controls sentence length, tenses, and vocabulary range — "
    "never exceed the level (no B1 vocabulary in an A1/G5 lesson)."
)

_LANG_RUSSIAN = (
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

LANGUAGE_RULES = {
    "english": _LANG_ENGLISH,
    "russian": _LANG_RUSSIAN,
    "_default": _LANG_UZBEK,
}

# Scaffolding-bridge phrasing, keyed by output_language (the medium).
_BRIDGE_CLAUSE = {
    "uz": 'formal Uzbek ("Siz")',
    "en": "formal English",
    "ru": 'formal Russian («Вы»)',
}
_BRIDGE_NAME = {"uz": "Uzbek", "en": "English", "ru": "Russian"}
_L2_BASE = {"english": _LANG_ENGLISH, "russian": _LANG_RUSSIAN}


def _l2_rule(target_lang: str, bridge_medium: str) -> str:
    """L2 target-language rule with the scaffolding BRIDGE in `bridge_medium`.
    target_lang in {"english","russian"}; bridge_medium in {"uz","en","ru"}.

    For "uz" (or any unknown medium) returns the frozen base VERBATIM, so the
    uz path is byte-identical to the legacy block by construction. For "en"/"ru"
    it substitutes the bridge phrases on that frozen base — it never rebuilds the
    text, so it cannot silently "fix" the base's authoring asymmetry (English's
    governing line says "in Uzbek", Russian's says "in formal Uzbek").

    This base stays frozen (un-freeze 2026-07-23 only appends a label clause in
    `_resolve_language_rule`, below this function — it never touches this base)."""
    base = _L2_BASE[target_lang]
    if bridge_medium == "uz" or bridge_medium not in _BRIDGE_CLAUSE:
        return base
    bridge = _BRIDGE_CLAUSE[bridge_medium]
    name = _BRIDGE_NAME[bridge_medium]
    # Order matters: replace the "formal Uzbek (…)" phrase first (scaffolding line
    # + Russian's governing line), then the bare "Uzbek (…)" (English's governing
    # line). "native-Uzbek learners" / "the Uzbek national curriculum" don't match
    # either pattern and are left intact.
    out = base.replace('formal Uzbek ("Siz")', bridge).replace('Uzbek ("Siz")', bridge)
    out = out.replace("(the UZ bridge)", f"(the {name} bridge)")
    out = out.replace("translate idiomatically into Uzbek",
                      f"translate idiomatically into {name}")
    return out

# --- Medium-of-instruction rules (whole-output language; decision 2026-06-29) ---
# Distinct from LANGUAGE_RULES above, which are the L2 *target-language* rules for
# the English/Russian CLASS subjects. MEDIUM_RULES govern the medium of instruction
# for every OTHER subject. "uz" reuses _LANG_UZBEK so this BASE block is unchanged
# (un-freeze 2026-07-23 only appends a label clause on top, in
# `_resolve_language_rule` below — this base itself stays frozen).
_MEDIUM_ENGLISH = (
    "All student-facing text in natural, formal English. Address the student "
    "respectfully as \"you\"; never slang or childish phrasing. "
    "Simplify the WORDING around the subject, not the subject itself: never change "
    "any formula, number, unit, date, fact, or answer logic to make text easier. "
    "Preserve every term, formula, number, unit, and symbol exactly as in the "
    "source; for a difficult term, keep it and add a short plain-language gloss "
    "rather than deleting it. Write natural English — avoid word-for-word calques "
    "from the source language. Split long sentences at logical points, but avoid "
    "robotic sentence-chopping. Modern, professional (non-casual) contexts."
)

_MEDIUM_RUSSIAN = (
    "All student-facing text in natural, formal Russian. Use the polite «Вы» "
    "register throughout; never the informal «ты», and avoid childish or slang "
    "phrasing. Simplify the WORDING around the subject, not the subject itself: "
    "never change any formula, number, unit, date, fact, or answer logic to make "
    "text easier. Preserve every term, formula, number, unit, and symbol exactly "
    "as in the source; for a difficult term, keep it and add a short plain-language "
    "gloss rather than deleting it. Write natural Russian — avoid word-for-word "
    "calques from the source language. Split long sentences at logical points, but "
    "avoid robotic sentence-chopping. Modern, professional (non-casual) contexts."
)

MEDIUM_RULES = {
    "uz": _LANG_UZBEK,          # base BLOCK byte-identical to the legacy `_default`
    "en": _MEDIUM_ENGLISH,
    "ru": _MEDIUM_RUSSIAN,
}

# Appended to the language rule for en/ru media. The shared prompt bodies name
# their sections/roles with English structural labels ("Scenario", "Role",
# "Why/How/What", "Checkpoint", "Learning Block", "Completion status") and the
# subject label is injected bilingually ("Mathematics (Matematika)"). Left alone,
# the model echoes those English/Uzbek strings into ru/en output. This directive
# tells it to localize them. uz gets its OWN clause instead — see
# `_LOCALIZE_HEADINGS_CLAUSE_UZ` below (un-freeze 2026-07-23, user-approved): the
# same leak happens into uz output (English labels surviving untranslated), and
# the MEDIUM_RULES base block above stays byte-identical — only this appended
# clause is new.
_LOCALIZE_HEADINGS_CLAUSE = (
    "\nEVERY label the student READS is part of the output language: render each "
    "section heading, the phase title, game labels (\"How to play\", \"Scenario\", "
    "\"Role\", \"Task\", \"Relationship types\", \"Why/How/What\", \"Checkpoint\", "
    "\"Learning Block\", \"Completion status\"), the feedback labels "
    "(Correct/Partial/Wrong), and the subject name in the output language. Do NOT "
    "copy the parenthetical source-language subject name (e.g. write the localized "
    "subject name, not \"Matematika\"). EXCEPTION — machine-facing keys stay exactly "
    "as the format defines them, in English: card field keys (id, front, back, type, "
    "difficulty, hint, explanation, example, misconception) and enum values in "
    "backticks (`easy`, `medium`, `hard`, card/relationship type names)."
)

# uz-specific counterpart to _LOCALIZE_HEADINGS_CLAUSE (un-freeze 2026-07-23,
# user-approved). Same leak, uz phrasing: student-read labels (section headings,
# game labels, feedback labels) must render in Uzbek, not linger in English.
# Machine-facing keys/enum values are explicitly carved out.
_LOCALIZE_HEADINGS_CLAUSE_UZ = (
    "\nEVERY label the student READS is part of the output language: render section "
    "headings, the phase title, game labels (\"How to play\", \"Scenario\", \"Role\", "
    "\"Task\", \"Relationship types\", \"Why/How/What\", \"Checkpoint\", \"Learning "
    "Block\"), and the feedback labels (Correct/Partial/Wrong) in Uzbek — never leave "
    "them in English. EXCEPTION — machine-facing keys stay exactly as the format "
    "defines them, in English: card field keys (id, front, back, type, difficulty, "
    "hint, explanation, example, misconception) and enum values in backticks "
    "(`easy`, `medium`, `hard`, card/relationship type names)."
)


def _resolve_language_rule(subject: str, output_language: str) -> str:
    """L2 language-class subjects (English/Russian) keep their L2 TARGET regardless
    of medium, but their scaffolding BRIDGE follows the chosen medium
    (l2-bridge-follows-medium). Every other subject renders in the chosen medium
    (uz/en/ru), defaulting uz. A heading/label-localization directive is appended
    for every medium: en/ru get `_LOCALIZE_HEADINGS_CLAUSE` (also covers the
    source-language subject-name leak); uz gets `_LOCALIZE_HEADINGS_CLAUSE_UZ`
    (un-freeze 2026-07-23, user-approved — PR #83 originally froze uz byte-identical
    to legacy; the base blocks above stay frozen, only this appended clause is new)."""
    sd = subjects.REGISTRY.get(subject)
    if sd and sd.language in ("english", "russian"):
        rule = _l2_rule(sd.language, output_language)
    else:
        rule = MEDIUM_RULES.get(output_language, MEDIUM_RULES["uz"])
    if (output_language or "").lower() in ("en", "ru"):
        rule = rule + _LOCALIZE_HEADINGS_CLAUSE
    else:                        # uz / default — un-freeze (2026-07-23, user-approved)
        rule = rule + _LOCALIZE_HEADINGS_CLAUSE_UZ
    return rule

# Derived from the single source of truth (app/services/subjects.py). A family of
# "default" has no FAMILY_RULES block and falls through to "_default".
_SUBJECT_FAMILY = {c: d.family for c, d in subjects.REGISTRY.items()}

# --- Case-Based Preview family blocks (injected at {{FAMILY_RULES}}) ---------
# Each ~12-25 lines: visual policy + case framing + family forbids. Ported from
# docs/Infra_prompts/Case-Based Preview/*. Humanities has no CBP spec — authored
# by extrapolating the 3 specs + the humanities Flashcards visual policy.

_CBP_SCIENCES = (
    "**Visual policy:** Do NOT emit `<svg>` or image markup — describe every visual "
    "as a placeholder the runtime renders: `![visual: <diagram|photo> — <what to "
    "depict, with every label/value/axis> — image gen required](placeholder)`. Prefer "
    "a *photo* placeholder for real labs, organisms, equipment, and phenomena, and a "
    "*diagram* placeholder for the conceptual layer (particle/molecular diagrams, "
    "force vectors, process flowcharts, before/after states). Default to no visual "
    "unless it genuinely carries the concept; never fabricate an image or invent a "
    "URL.\n\n"
    "**Case framing:** Physics — phenomenon → prediction → law/logic → consequence "
    "(observer, engineer, technician verifying a circuit/motion/optics setup). "
    "Chemistry — safety/observation → particle/reaction logic → result (lab "
    "assistant or analyst checking labels, classifying a substance, predicting a "
    "reaction). Biology — observation → system/process → mechanism → prediction "
    "(researcher or ecologist predicting an organism's response).\n\n"
    "**Avoid:** fictionalising a real reaction or phenomenon (no unicorn refraction, "
    "no magic-potion pH); oversimplifying chemistry safety; narrowing organism-wide "
    "concepts (photosynthesis, respiration) to human-only examples unless the topic "
    "is explicitly human biology; copying textbook artwork; cramming a multi-step "
    "mechanism into one dense visual instead of a few staged placeholders."
)

_CBP_MATH = (
    "**Visual policy:** Do NOT emit `<svg>` or image markup — describe every visual "
    "as a placeholder the runtime renders: `![visual: <diagram|photo> — <what to "
    "depict, with every label/value/axis> — image gen required](placeholder)`. Prefer "
    "a *diagram* placeholder for fraction/area bars, coordinate planes, graphs, "
    "geometric figures (triangles, polygons, circles, angles, constructions), number "
    "lines, formula visualisations, and step-by-step states; a *photo* placeholder "
    "only for genuine real-world context. Default to no visual unless it genuinely "
    "carries the concept; never fabricate an image or invent a URL.\n\n"
    "**Case framing:** practical sharing (a helper distributing a quantity — which "
    "operation?); money/measurement (a shopkeeper, builder, or gardener choosing the "
    "right arithmetic step or formula); error detection (a reviewer spotting which "
    "step in someone's work is wrong). Geometry has the strongest visual demand — "
    "every figure, angle, or construction needs a diagram placeholder.\n\n"
    "**Avoid:** changing numbers, variables, formulas, units, or calculation order; "
    "decorative visuals that don't carry the actual problem; the 'dragon needs algebra' "
    "trap (the math must be load-bearing); copying textbook artwork; placeholders with "
    "vague labels; unverified domain restrictions for rational expressions; asserting "
    "a geometric property (diagonal lengths, perpendicularity, angle sums, or polygon "
    "class membership) without the standard condition that makes it true; choosing the "
    "wrong medium for the math."
)

_CBP_LANGUAGES = (
    "**Visual policy:** Do NOT emit `<svg>` or image markup — describe every visual "
    "as a placeholder the runtime renders: `![visual: <diagram|photo> — <what to "
    "depict, with every label/value/axis> — image gen required](placeholder)`. Prefer "
    "a *photo* placeholder for the communicative scene (who is talking, where, how "
    "formal — classroom, market, café, office), and a *diagram* placeholder for the "
    "linguistic layer (sentence-structure blocks, word-order, wrong → corrected "
    "comparisons, register cards, tense timelines, conjugation tables). Default to no "
    "visual unless it genuinely carries the concept; never fabricate an image or "
    "invent a URL.\n\n"
    "**Case framing:** write a message (a student writing a polite note to a teacher "
    "— which tense/register?); fix grammar (an editor choosing the form that fits); "
    "choose register or respond to a situation (a speaker picking formal vs informal "
    "phrasing). The consequence is the **communication outcome** — the message lands, "
    "or it is unclear/rude/ungrammatical and conveys the wrong meaning.\n\n"
    "**Avoid:** grammar above the target CEFR/grade level; authoring a fresh passage "
    "when the textbook has one; magic-mirror/forest-spirit frames where the language "
    "is decorative; MCQ options that leak the answer via length or obvious formality; "
    "Russian/English calques (translate idiomatically, not word-for-word); cliché "
    "cowboy/cricket contexts unless the textbook is itself about them."
)

_CBP_HUMANITIES = (
    "**Visual policy:** Do NOT emit `<svg>` or image markup — describe every visual "
    "as a placeholder the runtime renders: `![visual: <diagram|photo> — <what to "
    "depict, with every label/value/axis> — image gen required](placeholder)`. Prefer "
    "a *diagram* placeholder for the structural layer (timelines, causal chains, "
    "labelled outline maps, comparison tables, dynasty/family trees), and a *photo* "
    "placeholder for real figures and objects (portraits, monuments, artifacts, "
    "photographic maps). Default to no visual unless it genuinely carries the "
    "concept; never fabricate an image or invent a URL.\n\n"
    "**Case framing:** historical decision (an advisor weighing a ruler's options); "
    "source/evidence check (a historian judging which source is strongest); "
    "cause/consequence dilemma (a witness tracing why an event followed another). "
    "The student is advisor, witness, historian, or source-checker — never a modern "
    "professional unless the era naturally fits.\n\n"
    "**Avoid:** invented causality — only assert 'A caused B' when the textbook does, "
    "otherwise present sequence without claimed causation; anachronistic state/place "
    "names (no modern country name for a pre-modern entity); one-sided framing of "
    "contested figures (mirror the textbook's stance, or stay neutral); misquoting "
    "primary sources (reproduce exactly or mark as paraphrase); decorative imagery "
    "instead of the specific monument/artifact the lesson is about."
)

_CBP_DEFAULT = (
    "**Visual policy:** Do NOT emit `<svg>` or image markup — describe every visual "
    "as a placeholder the runtime renders: `![visual: <diagram|photo> — <what to "
    "depict, with every label/value/axis> — image gen required](placeholder)`. Use a "
    "*diagram* placeholder for genuine figures, processes, charts, timelines, and "
    "before/after states, and a *photo* placeholder for a real-life scene or context. "
    "Default to no visual unless it genuinely carries the decision; never fabricate "
    "an image or invent a URL.\n\n"
    "**Case framing:** place the student as a decision-maker in a plausible, "
    "source-aligned situation where the lesson concept is the load-bearing reason "
    "the decision succeeds or fails.\n\n"
    "**Avoid:** decorative visuals that don't carry the concept; fantasy frames where "
    "the subject content is detachable; copying textbook artwork; inventing facts the "
    "textbook does not state."
)

# --- Flashcards family blocks (injected at {{FAMILY_RULES}}) ------------------
# Each ~12-25 lines: family card types (incl. extensions) + atomisation example
# + visual policy (with placeholder sentinel for IMAGE-default families) +
# family forbids. Ported from docs/Infra_prompts/Flashcards/Flashcard Prompts/*.

_FC_SCIENCES = (
    "**Card types:** core set plus `formula`. Use `definition`, `term_to_meaning`, "
    "`process_step`, `question_answer`, `misconception`, `image_label` (labelled "
    "anatomy / circuits / glassware / apparatus), and `formula` for equations and "
    "laws.\n\n"
    "**Atomisation:** photosynthesis becomes **6 cards**, not one paragraph back: "
    "(1) `definition` — what photosynthesis is; (2) `question_answer` — where it "
    "happens; (3) `question_answer` — inputs; (4) `question_answer` — outputs; "
    "(5) `formula` — the word equation `CO₂ + H₂O → glucose + O₂`; (6) `definition` "
    "— role of chlorophyll. One retrievable atom per card.\n\n"
    "**Visual policy:** Do NOT emit `<svg>` or image markup — describe every visual "
    "as a placeholder the runtime renders: `![visual: <diagram|photo> — <what to "
    "depict, with every label/value/axis> — image gen required](placeholder)`. Prefer "
    "a *photo* placeholder for real organisms, lab apparatus, and phenomena, and a "
    "*diagram* placeholder for the structural layer (equations, reaction mechanisms, "
    "force and circuit diagrams, process flowcharts, labelled schematics). Default to "
    "no visual unless it genuinely carries the recall; never fabricate an image or "
    "invent a URL.\n\n"
    "**Avoid:** biology cards that demand numerical calculation (biology is "
    "observation + explanation + prediction); physics formula cards that drop their "
    "units (`F = 10` is wrong; `F = 10 N` is complete); unbalanced chemistry "
    "equations; decorative clipart that does not label the actual structure; copying "
    "textbook artwork; placeholders with vague labels; lab-safety facts stuffed into a "
    "`misconception` field."
)

_FC_MATH = (
    "**Card types:** core set plus `formula`. Use `definition`, `formula`, "
    "`process_step`, `question_answer`, and `misconception`. The `example` type is "
    "rare here — most worked examples live inside another card's `example` field.\n\n"
    "**Atomisation:** the quadratic formula becomes **3 cards**, not one paragraph "
    "back: (1) `definition` — quadratic equation in standard form `ax² + bx + c = "
    "0`, `a ≠ 0`; (2) `formula` — the quadratic formula itself; (3) `misconception` "
    "— dropping the ± loses a root. Caveats go in `explanation` / `misconception`, "
    "never welded into `back`.\n\n"
    "**Visual policy:** Do NOT emit `<svg>` or image markup — describe every visual "
    "as a placeholder the runtime renders: `![visual: <diagram|photo> — <what to "
    "depict, with every label/value/axis> — image gen required](placeholder)`. Prefer "
    "a *diagram* placeholder for fraction/area bars, coordinate planes, graphs, "
    "geometric figures (triangles, polygons, circles, angles, constructions), number "
    "lines, and formula visualisations; a *photo* placeholder is usually overkill on "
    "an atomic flashcard. Default to no visual unless it genuinely carries the "
    "recall; never fabricate an image or invent a URL.\n\n"
    "**Avoid:** changing numbers, variables, formulas, units, or calculation order; "
    "word-problem flashcards (scenarios belong in the Case-Based Preview — flashcards "
    "are atomic facts); decorative visuals that don't carry the problem; placeholders "
    "with vague labels; copying textbook artwork; process-step cards that chain "
    "multiple sub-steps past 25 words (each step is its own card); unverified domain "
    "restrictions for rational expressions; asserting a geometric property (diagonal "
    "lengths, perpendicularity, angle sums, or polygon class membership) without the "
    "standard condition that makes it true; choosing the wrong medium for the math."
)

_FC_LANGUAGES = (
    "**Card types:** core set plus `vocabulary` and `grammar`. Use `vocabulary` "
    "(L2 word → L1 meaning), `grammar` (pattern → rule), `term_to_meaning`, "
    "`definition`, `misconception` (L1-interference / false friend), and "
    "`image_label` for picture vocabulary.\n\n"
    "**Atomisation:** Present Perfect plus its irregulars becomes **4 cards**, not "
    "one paragraph back: (1) `grammar` — the formula `have/has + V3`; (2) `grammar` "
    "— when to use Present Perfect; (3) `vocabulary` — past participle of *go* → "
    "*gone*; (4) `grammar` — `since` vs `for`. The English target sits on the "
    "front; the Uzbek bridge sits on back / hint / explanation.\n\n"
    "**Visual policy:** Do NOT emit `<svg>` or image markup — describe every visual "
    "as a placeholder the runtime renders: `![visual: <diagram|photo> — <what to "
    "depict, with every label/value/axis> — image gen required](placeholder)`. Prefer "
    "a *photo* placeholder for the communicative scene (classroom, market, café, "
    "office) and picture-vocabulary (`image_label`), and a *diagram* placeholder for "
    "the linguistic layer (sentence-structure blocks, conjugation tables, tense "
    "timelines, wrong → corrected comparisons). Default to no visual unless it "
    "genuinely carries the recall; never fabricate an image or invent a URL.\n\n"
    "**Avoid:** tenses, structures, or vocabulary above the target CEFR / grade "
    "level — not even inside example sentences; authoring a fresh passage when the "
    "textbook has one; cliché cowboy/cricket contexts unless the textbook is about "
    "them; Russian/English calques (translate idiomatically, not word-for-word); "
    "false-friend cards with no `misconception` warning; example sentences no native "
    "speaker would actually say."
)

_FC_HUMANITIES = (
    "**Card types:** core set. Use `definition`, `term_to_meaning`, "
    "`question_answer` (name → role, date → event, place → significance), "
    "`process_step` (one link of a causal chain per card), `misconception` "
    "(conflations between figures / eras / places), and `image_label` (portraits, "
    "maps, artifacts).\n\n"
    "**Atomisation:** Amir Temur becomes **6 cards**, not one paragraph back: "
    "(1) `question_answer` — his capital → Samarqand; (2) `question_answer` — year "
    "of death → 1405; (3) `definition` — the Timurid empire; (4) `image_label` — "
    "Bibi-Khanym mosque → his architectural commission; (5) `process_step` — his "
    "first major campaign; (6) `misconception` — Amir Temur is not Babur. A causal "
    "chain never lives on one card — split it into `process_step` links.\n\n"
    "**Visual policy:** Do NOT emit `<svg>` or image markup — describe every visual "
    "as a placeholder the runtime renders: `![visual: <diagram|photo> — <what to "
    "depict, with every label/value/axis> — image gen required](placeholder)`. Prefer "
    "a *diagram* placeholder for the structural layer (timelines, causal chains, "
    "labelled outline maps, comparison tables, dynasty/family trees), and a *photo* "
    "placeholder for real figures and objects (portraits, monuments, artifacts). "
    "Default to no visual unless it genuinely carries the recall; never fabricate an "
    "image or invent a URL.\n\n"
    "**Avoid:** invented causality — only write 'A caused B' when the textbook "
    "asserts it, otherwise present sequence without claimed causation; misquoted "
    "primary sources (reproduce exactly or mark the `example` as paraphrase); "
    "anachronistic state/place names for pre-modern entities; one-sided framing of "
    "contested figures (mirror the textbook, or stay neutral); geography statistics "
    "with no year; decorative imagery instead of the specific monument/artifact the "
    "lesson is about."
)

_FC_DEFAULT = (
    "**Card types:** the canonical core set — `definition`, `term_to_meaning`, "
    "`process_step`, `question_answer`, `misconception`, `image_label`. Add a "
    "`formula` type only if the lesson is genuinely formula-bearing.\n\n"
    "**Atomisation:** never pack a whole topic into one back. Split a multi-part "
    "concept into one card per retrievable atom — definition on one card, the rule "
    "or formula on another, the common error as its own `misconception` card — and "
    "move supporting context into the `explanation` / `example` / `misconception` "
    "fields.\n\n"
    "**Fidelity:** preserve subject-critical terms, numbers, formulas, and dates "
    "exactly as the textbook gives them; gloss hard terms in plainer words, never "
    "delete them.\n\n"
    "**Visual policy:** Do NOT emit `<svg>` or image markup — describe every visual "
    "as a placeholder the runtime renders: `![visual: <diagram|photo> — <what to "
    "depict, with every label/value/axis> — image gen required](placeholder)`. Use a "
    "*diagram* placeholder for genuine figures, processes, charts, and timelines, and "
    "a *photo* placeholder for a real-life object or scene. Default to no visual "
    "unless it genuinely carries the recall; never fabricate an image or invent a "
    "URL.\n\n"
    "**Avoid:** paragraph-length backs; folding explanation / example / misconception "
    "into `back`; decorative visuals that don't carry the concept; copying textbook "
    "artwork; inventing facts the textbook does not state."
)

# Family-varying prompt blocks, keyed [phase_name][family] with a phase-level
# "_default". Only CBP + flashcards vary by family; authored in Tasks 2-3.
# Resolution never leaks one family's block to another (see get_prompt).
FAMILY_RULES: dict[str, dict[str, str]] = {
    "case-based-preview": {
        "sciences": _CBP_SCIENCES,
        "math": _CBP_MATH,
        "languages": _CBP_LANGUAGES,
        "humanities": _CBP_HUMANITIES,
        "_default": _CBP_DEFAULT,
    },
    "flashcards": {
        "sciences": _FC_SCIENCES,
        "math": _FC_MATH,
        "languages": _FC_LANGUAGES,
        "humanities": _FC_HUMANITIES,
        "_default": _FC_DEFAULT,
    },
}

# Cache keys are (prompt_set_id, dirname) so different prompt sets never share
# a slot even when they both use the "_general" dirname (or, in the dormant
# USE_SUBJECT_PROMPTS override layer, the same subject dirname).
_cache: dict[tuple[str, str], dict[str, str]] = {}
_hash_cache: dict[tuple[str, str], dict[str, str]] = {}


def _root_for(prompt_set_id: str) -> Path:
    return prompt_sets.get_prompt_set(prompt_set_id).root


def _resolve_dir(root: Path, subject: str, phase_name: str) -> str:
    if USE_SUBJECT_PROMPTS and (root / subject / f"{phase_name}.md").is_file():
        return subject
    return GENERAL_DIR


def _load_dir(root: Path, dirname: str) -> tuple[dict[str, str], dict[str, str]]:
    d = root / dirname
    if not d.is_dir():
        raise FileNotFoundError(f"Prompt directory not found: {d}")
    bodies: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for md in d.glob("*.md"):
        body = md.read_text(encoding="utf-8")
        bodies[md.stem] = body
        hashes[md.stem] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return bodies, hashes


def load_all(prompt_set_id: str = LEGACY_PROMPT_SET_ID) -> None:
    root = _root_for(prompt_set_id)
    dirs = {GENERAL_DIR}
    if USE_SUBJECT_PROMPTS:
        from app.services.flows import SUPPORTED_SUBJECTS
        dirs.update(SUPPORTED_SUBJECTS)
    for dirname in dirs:
        bodies, hashes = _load_dir(root, dirname)
        _cache[(prompt_set_id, dirname)] = bodies
        _hash_cache[(prompt_set_id, dirname)] = hashes


def _raw(prompt_set_id: str, dirname: str, phase_name: str) -> tuple[str, str]:
    key = (prompt_set_id, dirname)
    if key not in _cache:
        root = _root_for(prompt_set_id)
        bodies, hashes = _load_dir(root, dirname)
        _cache[key] = bodies
        _hash_cache[key] = hashes
    if phase_name not in _cache[key]:
        raise KeyError(f"Prompt {prompt_set_id}/{dirname}/{phase_name}.md not found")
    return _cache[key][phase_name], _hash_cache[key][phase_name]


def _apply_substitutions(body: str, subject: str, output_language: str) -> str:
    """Shared `{{SUBJECT}}` / `{{LANGUAGE_RULES}}` substitution.

    Used by both `get_prompt` (the markdown evaluation contract) and
    `get_structured_prompt` (the JSON-authoring contract) so the two can never
    drift apart on how these two tokens resolve. `{{FAMILY_RULES}}` is NOT
    handled here — it's phase-family-specific and only `get_prompt` needs it.
    """
    body = body.replace("{{SUBJECT}}", SUBJECT_LABELS.get(subject, subject))
    body = body.replace("{{LANGUAGE_RULES}}",
                        _resolve_language_rule(subject, output_language))
    return body


def get_prompt(subject: str, phase_name: str, provider_suffix: str = "",
               output_language: str = "uz", *,
               prompt_set_id: str = LEGACY_PROMPT_SET_ID) -> str:
    root = _root_for(prompt_set_id)
    dirname = _resolve_dir(root, subject, phase_name)
    body, _h = _raw(prompt_set_id, dirname, phase_name)
    body = _apply_substitutions(body, subject, output_language)
    phase_blocks = FAMILY_RULES.get(phase_name, {})
    family = _SUBJECT_FAMILY.get(subject)
    family_block = phase_blocks.get(family) or phase_blocks.get("_default", "")
    body = body.replace("{{FAMILY_RULES}}", family_block)
    if provider_suffix:
        body = body + "\n\n" + provider_suffix
    return body


def get_structured_prompt(
    subject: str, phase: str, *, output_language: str = "uz",
    prompt_set_id: str = LEGACY_PROMPT_SET_ID,
) -> "str | None":
    """The JSON-authoring prompt for a structured phase, or None if it has none.

    Separate from `get_prompt`: that one is the MARKDOWN evaluation contract the
    judge, solver and lint read, and it says "Markdown only". A single prompt
    cannot both demand JSON and serve as the markdown contract. Structured
    prompts live under `<prompt set root>/_general/structured/` — a
    subdirectory of `_general`, deliberately not picked up by the `*.md`
    (non-recursive) globs that scan `_general` itself for the markdown
    contracts.
    """
    root = _root_for(prompt_set_id)
    path = root / "_general" / "structured" / f"{phase}.md"
    if not path.exists():
        return None
    body = path.read_text(encoding="utf-8")
    return _apply_substitutions(body, subject, output_language)


# Keyed by prompt_set_id -- each prompt set carries its own fidelity contract
# text, and the cache must never serve one set's bytes for another's id.
_teacher_deck_fidelity_cache: dict[str, str] = {}


def get_teacher_deck_fidelity_contract(
    *, prompt_set_id: str = LEGACY_PROMPT_SET_ID
) -> str:
    """The judge contract for grading a generated teacher deck's factual fidelity.

    Deliberately NOT reachable via `get_prompt(subject, "teacher-deck")` — that
    lookup only scans `_general/*.md` (non-recursive), and this file lives
    under the `structured/` subfolder alongside the authoring prompt, same as
    `get_structured_prompt`. Unlike the authoring prompt, this is loaded
    verbatim (no `{{SUBJECT}}`/`{{LANGUAGE_RULES}}` substitution — it's an
    English-only reviewer instruction, not student-facing content) and passed
    explicitly by the caller as `phase_judge.judge(..., contract_override=...)`.
    """
    if prompt_set_id not in _teacher_deck_fidelity_cache:
        root = _root_for(prompt_set_id)
        path = root / "_general" / "structured" / "teacher-deck.fidelity.md"
        _teacher_deck_fidelity_cache[prompt_set_id] = path.read_text(encoding="utf-8")
    return _teacher_deck_fidelity_cache[prompt_set_id]


def get_prompt_hash(subject: str, phase_name: str, output_language: str = "uz", *,
                     prompt_set_id: str = LEGACY_PROMPT_SET_ID) -> str:
    # Provenance only (recorded on agent_usages); does NOT drive cross-job reuse.
    import hashlib
    return hashlib.sha256(
        get_prompt(subject, phase_name, output_language=output_language,
                   prompt_set_id=prompt_set_id).encode("utf-8")
    ).hexdigest()
