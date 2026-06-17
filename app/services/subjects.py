"""Single source of truth for the subjects the system supports.

Every supported school subject (Uzbek national curriculum, grades 1-11) is one
`SubjectDef` here. `flows`, `prompts`, `notion_fetch`, and the frontend all
derive their subject tables from this registry, so adding a subject is a single
entry. Adding/removing a subject? Edit `REGISTRY` (and mirror the code list in
`web/src/lib/types.ts` / `subjects.ts`).

Fields:
- `code`     internal slug + storage/routing key. Stable forever (DB rows use
             it). The first 7 are legacy codes kept verbatim.
- `label`    display label (English (Original)).
- `family`   prompt family block: sciences | math | languages | humanities |
             default. "default" falls through to the `_default` family block.
- `game`     the subject-matched practice phase (must have a _general prompt).
- `language` language-rule key: uz (-> Uzbek default) | english | russian.
- `keywords` folded (lowercase, apostrophe-stripped) Uzbek substrings used to
             map a Notion subject-page title to this code. See
             `notion_keyword_pairs()` — matched longest-first so a compound
             title (e.g. "jismoniy tarbiya") wins over a bare one ("tarbiya").
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubjectDef:
    code: str
    label: str
    family: str
    game: str
    language: str
    keywords: tuple[str, ...]


_MEMORY = "practice-memory-match"
_TICTACTOE = "practice-tictactoe"
_JIGSAW = "practice-jigsaw"
_SENTENCE = "practice-sentence"


def _d(code, label, family, game, language, *keywords) -> SubjectDef:
    return SubjectDef(code, label, family, game, language, tuple(keywords))


# Order = display order. First 7 = legacy codes, preserved verbatim.
_DEFS: list[SubjectDef] = [
    # --- legacy (do not change code/family/game/label) ---
    _d("biology", "Biology (Biologiya)", "sciences", _MEMORY, "uz", "biolog"),
    _d("english", "English", "languages", _SENTENCE, "english", "ingliz"),
    _d("geometriya-g7-11", "Geometry (Geometriya)", "math", _JIGSAW, "uz", "geometriya"),
    _d("history", "History (Tarix)", "humanities", _MEMORY, "uz",
       "ozbekiston tarixi", "jahon tarixi", "tarix"),
    _d("kimyo-g7-11", "Chemistry (Kimyo)", "sciences", _TICTACTOE, "uz", "kimyo"),
    _d("math-algebra", "Mathematics / Algebra (Matematika / Algebra)", "math",
       _TICTACTOE, "uz", "algebra"),
    _d("physics", "Physics (Fizika)", "sciences", _TICTACTOE, "uz", "fizika"),
    # --- new subjects ---
    _d("matematika", "Mathematics (Matematika)", "math", _TICTACTOE, "uz", "matematika"),
    _d("ona-tili", "Uzbek (Ona tili)", "languages", _SENTENCE, "uz", "ona tili"),
    _d("adabiyot", "Literature (Adabiyot)", "languages", _SENTENCE, "uz", "adabiyot"),
    _d("russian", "Russian (Rus tili)", "languages", _SENTENCE, "russian", "rus tili"),
    _d("oqish-savodxonligi", "Reading literacy (O'qish savodxonligi)", "languages",
       _SENTENCE, "uz", "oqish savodxonligi", "oqish"),
    _d("alifbe", "Alphabet (Alifbe)", "languages", _SENTENCE, "uz", "alifbe"),
    _d("tabiiy-fanlar", "Natural sciences (Tabiiy fanlar)", "sciences", _TICTACTOE,
       "uz", "tabiiy fanlar", "tabiatshunoslik", "tabiiy", "science"),
    _d("astronomiya", "Astronomy (Astronomiya)", "sciences", _TICTACTOE, "uz", "astronomiya"),
    _d("geografiya", "Geography (Geografiya)", "humanities", _MEMORY, "uz", "geografiya"),
    _d("informatika", "Informatics (Informatika)", "default", _MEMORY, "uz",
       "informatika", "dasturlash", "robototexnika"),
    _d("atrof-muhit", "Environmental studies (Atrof-muhit)", "sciences", _TICTACTOE,
       "uz", "atrof-muhit", "atrof muhit"),
    _d("huquq", "Law (Huquq)", "humanities", _MEMORY, "uz", "huquq"),
    _d("iqtisodiyot", "Economics (Iqtisodiyot)", "humanities", _MEMORY, "uz",
       "iqtisodiy bilim", "iqtisodiyot", "tadbirkorlik"),
    _d("chizmachilik", "Technical drawing (Chizmachilik)", "math", _JIGSAW, "uz", "chizmachilik"),
]
# NOTE: registry intentionally lists only academically-testable subjects. The
# non-academic curriculum entries (jismoniy tarbiya/PE, musiqa, tasviriy san'at,
# texnologiya, tarbiya, odobnoma, ma'naviyat, kelajak soati, CHQBT) are excluded
# on purpose — they have no examinable subject-matter the homework pipeline can
# assess. Add one back as a `_d(...)` line here (+ FE mirror) if that changes.

REGISTRY: dict[str, SubjectDef] = {d.code: d for d in _DEFS}
SUBJECT_CODES: list[str] = [d.code for d in _DEFS]


def notion_keyword_pairs() -> list[tuple[str, str]]:
    """(folded-keyword, code) pairs sorted by descending keyword length so a
    compound title is matched before a bare substring it contains."""
    pairs = [(kw, d.code) for d in _DEFS for kw in d.keywords]
    pairs.sort(key=lambda kc: -len(kc[0]))
    return pairs
