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
    # Non-exam subjects that nonetheless ship real textbooks in Notion (so they
    # are launchable) — kept by user decision. PE is deliberately NOT here.
    _d("musiqa", "Music (Musiqa)", "default", _MEMORY, "uz", "musiqa"),
    _d("tasviriy-sanat", "Fine arts (Tasviriy san'at)", "default", _MEMORY, "uz", "tasviriy"),
    _d("texnologiya", "Technology (Texnologiya)", "default", _MEMORY, "uz", "texnologiya"),
    _d("tarbiya", "Upbringing (Tarbiya)", "humanities", _MEMORY, "uz", "tarbiya"),
    _d("chqbt", "Pre-conscription training (CHQBT)", "humanities", _MEMORY, "uz", "chqbt"),
]
# Excluded subjects (NOT registered): PE (jismoniy tarbiya — has textbooks but
# excluded by decision), and odobnoma/axloqiy-tarbiya, ma'naviyat, kelajak soati
# (no textbook in Notion). The first two below also SHADOW the bare "tarbiya"
# keyword above (substring match), so `_map_subject` must reject them FIRST or a
# PE/Ethics page would mis-map to Upbringing. Folded (lowercase, no apostrophes).
EXCLUDED_KEYWORDS: tuple[str, ...] = ("jismoniy tarbiya", "axloqiy tarbiya")

REGISTRY: dict[str, SubjectDef] = {d.code: d for d in _DEFS}
SUBJECT_CODES: list[str] = [d.code for d in _DEFS]


def notion_keyword_pairs() -> list[tuple[str, str]]:
    """(folded-keyword, code) pairs sorted by descending keyword length so a
    compound title is matched before a bare substring it contains."""
    pairs = [(kw, d.code) for d in _DEFS for kw in d.keywords]
    pairs.sort(key=lambda kc: -len(kc[0]))
    return pairs


# History is one app-subject ("history") but splits into two Notion pages /
# textbooks: Jahon tarixi (World) and O'zbekiston tarixi (national). The variant
# is recoverable from the book filename via the SAME folded-keyword basis the
# Notion archive split uses (notion_archive._resolve_subject_page_id), so the UI
# label and the archive routing never disagree. Display-only — not persisted.
_VARIANT_APOSTROPHES = "'‘’ʻ`"
# (folded-keyword, variant-key). A filename carries at most one in practice;
# order only decides a pathological both-match. jahon-first mirrors the majority
# of the per-grade NOTION_SUBJECT_PAGES dicts (archive routing).
_HISTORY_VARIANT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("jahon", "jahon"),
    ("ozbekiston", "ozbekiston"),
)


def history_variant(subject: str, filename: str | None) -> str | None:
    """For a history book, the variant key ("jahon"|"ozbekiston") derived from the
    filename, else None. None for non-history subjects, a combined Ancient-World
    book (Tarix qadimgi dunyo), or a missing/ambiguous filename."""
    if subject != "history" or not filename:
        return None
    folded = filename.lower().translate({ord(c): None for c in _VARIANT_APOSTROPHES})
    for keyword, variant in _HISTORY_VARIANT_KEYWORDS:
        if keyword in folded:
            return variant
    return None
