import hashlib
from pathlib import Path

from app.services import subjects

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
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

# --- Notation blocks (injected at {{NOTATION_RULES}}, all 11 phases) ----------
# The import platform renders homework as PLAIN TEXT (no KaTeX/markdown on the
# student game surfaces), so every phase prompt carries a render-ready notation
# contract: Unicode math instead of LaTeX, plus the platform's import/scrubber
# bans. Two variants: exact sciences get the full math standard; prose subjects
# a lighter one. Family resolution mirrors FAMILY_RULES ("math"/"sciences" vs
# the rest).

_NOTATION_EXACT = (
    "## Notation & rendering (MANDATORY)\n"
    "\n"
    "The student app displays this content as PLAIN TEXT — there is no formula "
    "renderer. Whatever you write is shown character-for-character. LaTeX/TeX is "
    "FORBIDDEN: never output $ or $$ delimiters and never backslash commands "
    "(\\frac, \\cdot, \\times, \\neq, \\text, \\sqrt, \\le, \\begin — none of "
    "them). Write every expression in Unicode text a student reads directly:\n"
    "- Powers and indices: x², x³, 10⁵, aⁿ; subscripts a₁, b₂, x₀; chemistry "
    "H₂O, CO₂, Ca(OH)₂.\n"
    "- Operators: × ÷ ± ≠ ≤ ≥ ≈ → ·. Strict inequalities keep < and > with a "
    "space on BOTH sides (2 < 5, x > −1); never glue < or > to a letter and "
    "never wrap words in angle brackets (<so'z>) — that reads as an HTML tag "
    "and gets destroyed. Never a bare & — write \"va\".\n"
    "- Roots: √16, √(x + 1) — parenthesize multi-term radicands; π stays π; "
    "degrees 45°.\n"
    "- Fractions on ONE line with a slash and parentheses: (2x − 6)/(9 − x²), "
    "a₁/a₂ ≠ b₁/b₂. Never stack or nest fractions — restructure into sequential "
    "steps instead.\n"
    "- Systems of equations: one equation per line, each line starting with "
    "\"{ \".\n"
    "- Thousands with spaces (4 500 000); units as plain words/symbols "
    "(so'm, kg, m/s).\n"
    "- Money: NEVER the $ symbol — write \"5 dollar\", \"5 000 so'm\".\n"
    "\n"
    "Output hygiene (hard bans in the OUTPUT): no double underscores (__); no "
    "✓ ✔ ✅ marks; no line may start with \"izoh:\", \"asos:\" or a bare "
    "\"javob:\" — the answer labels this format explicitly defines are the only "
    "exception. Inside option text: no \"A)\"-style letter prefixes (the option "
    "line's own letter is the only letter), and never \"to'g'ri\"/\"noto'g'ri\" "
    "immediately after a dash — rephrase so terms like \"to'g'ri chiziq\" never "
    "follow a dash. Any correct-answer text must repeat its option text "
    "character-for-character."
)

_NOTATION_PROSE = (
    "## Notation & rendering (MANDATORY)\n"
    "\n"
    "The student app displays this content as PLAIN TEXT — there is no formula "
    "or symbol renderer. LaTeX/TeX is FORBIDDEN: never output $ or $$ "
    "delimiters or backslash commands. Write numbers, percentages, dates and "
    "simple expressions as plain text (×, ÷, ±, ≠, ≤, ≥, →, x², H₂O); "
    "thousands with spaces (4 500 000).\n"
    "- Money: NEVER the $ symbol — write \"5 dollar\", \"10 funt\", "
    "\"5 000 so'm\".\n"
    "- Never wrap words in angle brackets (<so'z>) and never a bare & — "
    "angle-bracket runs read as HTML tags and get destroyed; if a comparison "
    "sign is needed keep spaces on both sides (2 < 5).\n"
    "\n"
    "Output hygiene (hard bans in the OUTPUT): no double underscores (__); no "
    "✓ ✔ ✅ marks; no line may start with \"izoh:\", \"asos:\" or a bare "
    "\"javob:\" — the answer labels this format explicitly defines are the only "
    "exception. Inside option text: no \"A)\"-style letter prefixes, never "
    "\"to'g'ri\"/\"noto'g'ri\" immediately after a dash, and any correct-answer "
    "text must repeat its option text character-for-character."
)

# --- Case-Based Preview family blocks (injected at {{FAMILY_RULES}}) ---------
# Each ~12-25 lines: visual policy + case framing + family forbids. Ported from
# docs/Infra_prompts/Case-Based Preview/*. Humanities has no CBP spec — authored
# by extrapolating the 3 specs + the humanities Flashcards visual policy.

_CBP_SCIENCES = (
    "**Visual policy:** *photo* for labs, organisms, phenomena; *diagram* for the "
    "conceptual layer (particles, forces); none unless it carries the concept.\n\n"
    "**Case framing** (never apply one science's flow, DPE, or distractor rule to "
    "another's): Physics — the frame truly in doubt; the prediction (which "
    "quantity changes, which does not) comes before any number; derivation steps "
    "carry reasons, not assertions; distractors hold the situation, vary only "
    "the reference body; DPE: the frame used · why it · the other frame's answer. "
    "Chemistry — a case appearance cannot settle; observation → deciding "
    "criterion → particles → formula LAST; distractors hold the observation, vary "
    "whether the composition changed; DPE: the deciding observation · why it "
    "decides, not appearance · appearance's answer. Biology — a system under "
    "change, level (cell/organ/organism/population) unnamed in the setup; "
    "checkpoint 1 MUST ask which organizational level the change belongs to, its "
    "wrong options being the other levels; then level → structure → function → "
    "prediction; never a formula biology does not have; distractors hold the "
    "structure, vary the level; DPE: the level asked · the mechanism there · its "
    "failure at another level. Other sciences: whichever frame fits, or the "
    "general framing. Every DPE closing note names the SYSTEM as the evaluator "
    "in active voice (\"tizim ... o'qib chiqib baholaydi\") — a bare passive "
    "\"baholanadi\" is not an assertion.\n\n"
    "**Hard lines:** a corrupted chemical formula (CaCl for CaCl₂) never appears "
    "as an MCQ option — exposure teaches it; a corrupted-formula common mistake "
    "gets a non-formula distractor. Every quantity carries its unit; every "
    "shown equation balances. Biology: no purpose language (\"uchun\") anywhere — "
    "function is what a structure does and what follows.\n\n"
    "**Avoid:** oversimplified safety; organism-wide concepts narrowed "
    "to human-only unless the topic is human biology; copying textbook artwork."
)

_CBP_MATH = (
    "**Visual policy:** *diagram* placeholders — bars, coordinate planes, graphs, "
    "figures, number lines, step states; every geometry figure gets one; *photo* "
    "only for real-world context; none unless it carries the concept.\n\n"
    "**Case framing:** practical sharing, money/measurement, or error detection. A "
    "mathematics case MUST open, inside one of these shapes, on a contested value — "
    "two answers that cannot both be right (a checker vs a total), structural, never "
    "settled by a keyword. Delete every sentence naming a method, rule, or "
    "operation: a decision the student can get wrong must remain, or it is a worked "
    "example in case's clothes — rebuild. A checkpoint answerable without noticing "
    "the structure is drill — rebuild.\n\n"
    "**Distractors:** hold the operand, vary the operator or parameter — never four "
    "number-sets under one operation; the dimension options differ on — extending, "
    "never replacing, anti-leak.\n\n"
    "**Teaching:** shortcuts state their bound — \"remove as many zeros as the "
    "divisor has\" gives 450 for 45 000 ÷ 500 (true value 90); never "
    "PEMDAS/BODMAS/BIDMAS or any translation — precedence is the textbook's "
    "I/II/III bosqich tiers; one glyph per operation, per the source "
    "(`:` vs `÷`, `·` vs `×`); both simulation paths run to a value, the separating "
    "rule named, the dispute settled.\n\n"
    "**Avoid:** copying textbook artwork; a geometric property asserted without "
    "the condition that makes it true.\n\n"
    "**DPE (mathematics):** which rule governs, known before computing · why it, "
    "not the rejected one · the common mistake's wrong result, and where the rule "
    "stops applying."
)

_CBP_LANGUAGES = (
    "**Visual policy:** *photo* for the communicative scene (who, to whom, how "
    "formal); *diagram* for the linguistic layer (word order, wrong → corrected); "
    "none unless it carries the concept.\n\n"
    "**Case framing:** a reader sits on the other end of the error; if nothing is "
    "misunderstood when the student errs, no case opened (\"fill in the "
    "blank\"). The consequence is the communication outcome — the failure state "
    "(the sent message), never a verdict. Instruction gives the form's "
    "conditioning environment, never a correlating cue (a time word); distractors "
    "hold the meaning, vary the conditioning environment — extending, never "
    "replacing, anti-leak.\n\n"
    "**Case shape follows the common mistake:** a wrong form with a "
    "visible cost → the cost-of-the-wrong-form case; two grammatical forms with "
    "different meanings → for English/Russian (L2) a meaning-contrast case (both "
    "grammatical, the two readings side by side), for Uzbek (Ona tili) an "
    "analysis case — take apart a sentence the student could have "
    "produced (tez mashina vs tez yurdi), naming its class or sentence part by "
    "behaviour (meaning-contrast is wrong for L1); a procedure → worked "
    "once, then run by the student. Literature (adabiyot, o'qish): an "
    "interpretation the passage supports — motive or theme from what it shows; "
    "quote exactly or mark paraphrase; never spoil what the textbook withholds. "
    "Zero malformed words in either language — the written form IS the content.\n\n"
    "**Avoid:** options leaking via formality; Russian/English calques.\n\n"
    "**DPE:** L2 — what the reader would take from the other form · what selected "
    "it · the wrong meaning the common mistake sends; Uzbek L1 — the form or "
    "class the sentence needed · the behaviour that told you · the misreading the "
    "mistake causes."
)

_CBP_HUMANITIES = (
    "**Visual policy:** *diagram* for the structural layer (timelines, maps, "
    "dynasty trees); *photo* for figures, objects; none unless it carries "
    "the concept.\n\n"
    "**History:** the case centres a source on a question the student must "
    "settle; who made it and why comes before what it says; sources display "
    "maker, date, primary/secondary; distractors hold the event and vary the "
    "SOURCE it is known through — at least two of the three checkpoints' option "
    "sets MUST differ by source (a chronicle vs a coin vs a traveler's account), "
    "not by fact. Plant exactly one true but irrelevant fact, named in "
    "feedback as the planted distractor; DPE: who made the source and "
    "why · what it can and cannot answer · which true fact was set aside — and "
    "the set-aside question itself never names that fact (asking \"which fact "
    "(X)?\" answers itself). The DPE closing note names the SYSTEM as the "
    "evaluator in active voice (\"tizim ... o'qib chiqib baholaydi\"), never a "
    "bare passive \"baholanadi\".\n\n"
    "**Economics:** a decision under a real constraint, in so'm, with instruments "
    "the student could meet (household budget, bazaar, family loan — never "
    "credit scores, mortgages, index funds); checkpoints: 1 the chooser and what "
    "is scarce for them · 2 the best foregone alternative, standpoint named · 3 "
    "why the wrong reading fails. A model is never a law — keep the textbook's "
    "modal verb (\"odatda\", \"-shi mumkin\"); mark everyday/technical divergences "
    "at first use (narx ≠ qiymat, foyda ≠ daromad, kredit ≠ qarz); no moralising "
    "(\"qarz olish yomon\" is not a fact); DPE: whose standpoint, what is scarce · "
    "the best foregone alternative · who would decide differently, what flips the "
    "answer.\n\n"
    "An arithmetic step exists only to compare two alternatives — never as a "
    "checkpoint of its own. Never claim the output measures the student's "
    "real-world conduct — a written case measures simulated judgement. A case "
    "may press its stakes onto the student's in-fiction professional role — "
    "never their out-of-fiction identity — and a pressured case still resolves "
    "through the standard consequence and feedback, naming the pressure.\n\n"
    "Law, upbringing, pre-conscription: the general "
    "decision/source/cause-consequence framing; for a law lesson, if the "
    "textbook may predate current law, flag rather than assert outdated law as "
    "current.\n\n"
    "**Avoid:** invented causality (\"A caused B\" only when the textbook asserts "
    "it; else sequence, no causation); anachronistic state/place names; "
    "one-sided framing of contested figures; misquoted sources (exact or marked "
    "paraphrase); money figures without so'm and year; "
    "geography statistics without the year."
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
    "the decision succeeds or fails. A Preview contains no fluency drill: if a "
    "checkpoint can be answered without noticing the lesson's structure, rebuild "
    "it.\n\n"
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
    "**Per-science:** physics — term cards only where the term is a prerequisite "
    "for the lesson's skill, never as trivia. Chemistry — classification cards "
    "drill the criterion, never the noun: the observation that decides "
    "acid/base/salt, not the category label; a corrupted chemical formula never "
    "appears anywhere on a card — front, back, hint, example, or misconception — "
    "a wrong subscript is a different substance, and exposure teaches it (this "
    "extends the unbalanced-equations rule below). Biology — retrieval on terms "
    "only where the term is a prerequisite, never the destination; no purpose "
    "language in any field; imagery anchors nomenclature (what this structure is "
    "called), never mechanism.\n\n"
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
    "**Deck arc:** a concept never ends on a `definition`/`term_to_meaning` card — "
    "recall opens a concept, never closes it; the model card targets the lesson's "
    "live misconception (× vs ÷ precedence order). The named failure: a deck whose "
    "first card is the Greek etymology of \"geometriya\". Every quantity carries "
    "its unit; one operator glyph per operation, following the source book; never "
    "PEMDAS/BODMAS/BIDMAS — precedence vocabulary is the textbook's I/II/III "
    "bosqich tiers.\n\n"
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
    "*gone*; (4) `grammar` — `since` vs `for`. The Uzbek bridge sits in hint / "
    "explanation; the front/back direction follows the cue-direction rule below.\n\n"
    "**Cue direction:** author it deliberately — at least half the deck runs "
    "productive: the front cues the meaning (the Uzbek bridge) and the back is the "
    "target-language form the student must produce; a deck that always cues "
    "target → Uzbek trains recognition only (the named failure: six of seven cards "
    "receptive). Zero malformed or corrupted strings in either language — the "
    "written form IS the content.\n\n"
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
    "speaker would actually say (read them aloud; rewrite any that fail)."
)

_FC_HUMANITIES = (
    "**Card types:** core set. Use `definition`, `term_to_meaning`, "
    "`question_answer` (name → role, date → event, place → significance), "
    "`process_step` (one link of SEQUENCE per card), `misconception` "
    "(conflations between figures / eras / places), and `image_label` (portraits, "
    "maps, artifacts).\n\n"
    "**Atomisation:** Amir Temur becomes **6 cards**, not one paragraph back: "
    "(1) `question_answer` — his capital → Samarqand; (2) `question_answer` — year "
    "of death → 1405; (3) `definition` — the Timurid empire; (4) `image_label` — "
    "Bibi-Khanym mosque → his architectural commission; (5) `process_step` — his "
    "first major campaign; (6) `misconception` — Amir Temur is not Babur. A chain "
    "of events never lives on one card — split it into `process_step` links.\n\n"
    "**History anchors are pairings** (name → role, date → event, event → what "
    "came next), never causal claims of their own — a `process_step` card carries "
    "one link of sequence, and causal wording appears on a card only in the "
    "textbook's own words. A title or polity type is never simplified into a "
    "general word (khan ≠ \"king\", khanate ≠ \"country\").\n\n"
    "**Economics:** the deck anchors vocabulary and is never the lesson's "
    "assessment — economics vocabulary reaches fluency with no model underneath it "
    "(the student who can decide and the one who can only recite both define "
    "\"muqobil qiymat\" correctly); the decision work lives in the other phases, "
    "so backs stay meanings — never decision scenarios on a card. A model is never "
    "written as a law: carry the textbook's modal verb (\"odatda\", \"-shi "
    "mumkin\") onto the back and put the flip condition in `explanation`. narx ≠ "
    "qiymat, foyda ≠ daromad, kredit ≠ qarz — never interchangeable; each "
    "everyday/technical divergence the lesson touches is itself a `misconception` "
    "card. Money figures carry so'm and the year. No moralising backs (\"qarz "
    "olish yomon\" is not a fact).\n\n"
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

_cache: dict[str, dict[str, str]] = {}
_hash_cache: dict[str, dict[str, str]] = {}


def _resolve_dir(subject: str, phase_name: str) -> str:
    if USE_SUBJECT_PROMPTS and (PROMPTS_DIR / subject / f"{phase_name}.md").is_file():
        return subject
    return GENERAL_DIR


def _load_dir(dirname: str) -> tuple[dict[str, str], dict[str, str]]:
    d = PROMPTS_DIR / dirname
    if not d.is_dir():
        raise FileNotFoundError(f"Prompt directory not found: {d}")
    bodies: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for md in d.glob("*.md"):
        body = md.read_text(encoding="utf-8")
        bodies[md.stem] = body
        hashes[md.stem] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return bodies, hashes


def load_all() -> None:
    dirs = {GENERAL_DIR}
    if USE_SUBJECT_PROMPTS:
        from app.services.flows import SUPPORTED_SUBJECTS
        dirs.update(SUPPORTED_SUBJECTS)
    for dirname in dirs:
        bodies, hashes = _load_dir(dirname)
        _cache[dirname] = bodies
        _hash_cache[dirname] = hashes


def _raw(dirname: str, phase_name: str) -> tuple[str, str]:
    if dirname not in _cache:
        bodies, hashes = _load_dir(dirname)
        _cache[dirname] = bodies
        _hash_cache[dirname] = hashes
    if phase_name not in _cache[dirname]:
        raise KeyError(f"Prompt {dirname}/{phase_name}.md not found")
    return _cache[dirname][phase_name], _hash_cache[dirname][phase_name]


def _apply_substitutions(body: str, subject: str, output_language: str) -> str:
    """Shared `{{SUBJECT}}` / `{{LANGUAGE_RULES}}` / `{{NOTATION_RULES}}`
    substitution.

    Used by both `get_prompt` (the markdown evaluation contract) and
    `get_structured_prompt` (the JSON-authoring contract) so the two can never
    drift apart on how these tokens resolve. `{{FAMILY_RULES}}` is NOT
    handled here — it's phase-family-specific and only `get_prompt` needs it.
    `{{NOTATION_RULES}}` resolves by subject family: exact sciences get the
    full Unicode-math standard, prose subjects the light variant (both defined
    next to `_SUBJECT_FAMILY` above).
    """
    body = body.replace("{{SUBJECT}}", SUBJECT_LABELS.get(subject, subject))
    body = body.replace("{{LANGUAGE_RULES}}",
                        _resolve_language_rule(subject, output_language))
    family = _SUBJECT_FAMILY.get(subject)
    body = body.replace("{{NOTATION_RULES}}",
                        _NOTATION_EXACT if family in ("math", "sciences")
                        else _NOTATION_PROSE)
    return body


def get_prompt(subject: str, phase_name: str, provider_suffix: str = "",
               output_language: str = "uz") -> str:
    dirname = _resolve_dir(subject, phase_name)
    body, _h = _raw(dirname, phase_name)
    body = _apply_substitutions(body, subject, output_language)
    phase_blocks = FAMILY_RULES.get(phase_name, {})
    family = _SUBJECT_FAMILY.get(subject)
    family_block = phase_blocks.get(family) or phase_blocks.get("_default", "")
    body = body.replace("{{FAMILY_RULES}}", family_block)
    if provider_suffix:
        body = body + "\n\n" + provider_suffix
    return body


def get_structured_prompt(
    subject: str, phase: str, *, output_language: str = "uz"
) -> "str | None":
    """The JSON-authoring prompt for a structured phase, or None if it has none.

    Separate from `get_prompt`: that one is the MARKDOWN evaluation contract the
    judge, solver and lint read, and it says "Markdown only". A single prompt
    cannot both demand JSON and serve as the markdown contract. Structured
    prompts live under `prompts/_general/structured/` — a subdirectory of
    `_general`, deliberately not picked up by the `*.md` (non-recursive) globs
    that scan `_general` itself for the markdown contracts.
    """
    path = PROMPTS_DIR / "_general" / "structured" / f"{phase}.md"
    if not path.exists():
        return None
    body = path.read_text(encoding="utf-8")
    return _apply_substitutions(body, subject, output_language)


_TEACHER_DECK_FIDELITY_PATH = (
    PROMPTS_DIR / "_general" / "structured" / "teacher-deck.fidelity.md"
)
_teacher_deck_fidelity_cache: str | None = None


def get_teacher_deck_fidelity_contract() -> str:
    """The judge contract for grading a generated teacher deck's factual fidelity.

    Deliberately NOT reachable via `get_prompt(subject, "teacher-deck")` — that
    lookup only scans `_general/*.md` (non-recursive), and this file lives
    under the `structured/` subfolder alongside the authoring prompt, same as
    `get_structured_prompt`. Unlike the authoring prompt, this is loaded
    verbatim (no `{{SUBJECT}}`/`{{LANGUAGE_RULES}}` substitution — it's an
    English-only reviewer instruction, not student-facing content) and passed
    explicitly by the caller as `phase_judge.judge(..., contract_override=...)`.
    """
    global _teacher_deck_fidelity_cache
    if _teacher_deck_fidelity_cache is None:
        _teacher_deck_fidelity_cache = _TEACHER_DECK_FIDELITY_PATH.read_text(
            encoding="utf-8"
        )
    return _teacher_deck_fidelity_cache


def get_prompt_hash(subject: str, phase_name: str, output_language: str = "uz") -> str:
    # Provenance only (recorded on agent_usages); does NOT drive cross-job reuse.
    import hashlib
    return hashlib.sha256(
        get_prompt(subject, phase_name, output_language=output_language).encode("utf-8")
    ).hexdigest()
