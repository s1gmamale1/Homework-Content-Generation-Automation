# All-Subjects Support — Single Subject Registry (Design)

**Date:** 2026-06-16
**Branch / worktree:** `feat/all-subjects-prompts` (worktree `/Users/macmini5/Documents/HCGA-wt-all-subjects`)
**Status:** self-approved (user away, explicit autonomy directive); P1 prereq of the Oct/Mar content campaign.

## Problem

The system only supports 7 subjects. New subjects are **hard-rejected** at:
- `flows.flow_for()` — `raise KeyError` when `subject not in SUBJECTS` (and `SUBJECT_GAME[subject]` KeyErrors).
- `app/api/v1/books.py:57-58, 247-248` — `if subject not in SUPPORTED_SUBJECTS: 400`.
- `notion_fetch._map_subject()` — returns `None` for unmapped Notion titles; the fleet launcher then **disables** that subject (`launcher.tsx:149,154` gate on `s.app_subject`). This is the real campaign-launch gate.
- `web/src/lib/types.ts` — `Subject` union + `SUBJECTS` array (compile-time).

**The prompt layer is already general.** `prompts.py` already has `_SUBJECT_FAMILY`, `FAMILY_RULES` (sciences/math/languages/humanities + `_default`), `LANGUAGE_RULES` (english L2 + Uzbek `_default`), and `SUBJECT_LABELS` — and `get_prompt` already falls back to `_default`/raw-subject for any unknown subject. The Infra_prompts family blocks are already ported in. **No prompt rewrite is required.** The work is the *registry*, not the prompts.

## Goal

Make every subject in the Uzbek national curriculum (grades 1–11) a first-class supported subject, driven by **one source of truth**, so adding a subject is a single registry entry. Correctly classify each subject into the existing family / language / game buckets so it gets the right (already-existing) prompt behavior. No regressions for the 7 existing subjects (their codes, families, games, labels are preserved verbatim — existing DB rows depend on the codes).

Non-goals (YAGNI / "don't overdo"): no per-subject prompt files; no new family prompt blocks (the 4 + default cover everything); no Russian-medium ("klass") track (notion_fetch reads the Uzbek "sinf" page only); no DB migration (subject is `String(64)`, unconstrained); no new family taxonomy (arts/ICT fold into `default`).

## Subject set (from Notion, grades 1–11)

30 canonical codes. Two history subjects (O'zbekiston tarixi + Jahon tarixi) intentionally **merge** into the existing `history` code (same humanities prompts; grade + book title carry the distinction; preserves existing books). Natural-science variants (Tabiiy fanlar / Tabiatshunoslik / Science) merge into `tabiiy-fanlar`. Economics variants merge into `iqtisodiyot`.

Family ∈ {sciences, math, languages, humanities, default}. `default` resolves to the existing `_default` family block. Game ∈ {practice-memory-match, practice-tictactoe, practice-jigsaw, practice-sentence} (all have prompt files). Language ∈ {uz, english, russian} (uz → Uzbek `_default` rule).

| code | label | family | game | lang | notion keywords (folded uz) |
|---|---|---|---|---|---|
| biology* | Biology (Biologiya) | sciences | practice-memory-match | uz | biolog |
| english* | English | languages | practice-sentence | english | ingliz |
| geometriya-g7-11* | Geometry (Geometriya) | math | practice-jigsaw | uz | geometriya |
| history* | History (Tarix) | humanities | practice-memory-match | uz | ozbekiston tarixi, jahon tarixi, tarix |
| kimyo-g7-11* | Chemistry (Kimyo) | sciences | practice-tictactoe | uz | kimyo |
| math-algebra* | Mathematics / Algebra | math | practice-tictactoe | uz | algebra |
| physics* | Physics (Fizika) | sciences | practice-tictactoe | uz | fizika |
| matematika | Mathematics (Matematika) | math | practice-tictactoe | uz | matematika |
| ona-tili | Uzbek (Ona tili) | languages | practice-sentence | uz | ona tili |
| adabiyot | Literature (Adabiyot) | languages | practice-sentence | uz | adabiyot |
| russian | Russian (Rus tili) | languages | practice-sentence | russian | rus tili |
| oqish-savodxonligi | Reading literacy | languages | practice-sentence | uz | oqish savodxonligi, oqish |
| alifbe | Alphabet (Alifbe) | languages | practice-sentence | uz | alifbe |
| tabiiy-fanlar | Natural sciences | sciences | practice-tictactoe | uz | tabiiy fanlar, tabiatshunoslik, tabiiy, science |
| astronomiya | Astronomy (Astronomiya) | sciences | practice-tictactoe | uz | astronomiya |
| geografiya | Geography (Geografiya) | humanities | practice-memory-match | uz | geografiya |
| informatika | Informatics (Informatika) | default | practice-memory-match | uz | informatika, dasturlash, robototexnika |
| atrof-muhit | Environmental studies | sciences | practice-tictactoe | uz | atrof-muhit, atrof muhit |
| huquq | Law (Huquq) | humanities | practice-memory-match | uz | huquq |
| iqtisodiyot | Economics (Iqtisodiyot) | humanities | practice-memory-match | uz | iqtisodiy bilim, iqtisodiyot, tadbirkorlik |
| manaviyat | Foundations of spirituality | humanities | practice-memory-match | uz | manaviyat |
| odobnoma | Ethics (Odobnoma) | humanities | practice-memory-match | uz | odobnoma, axloqiy tarbiya |
| tarbiya | Upbringing (Tarbiya) | humanities | practice-memory-match | uz | tarbiya |
| kelajak-soati | Future hour | humanities | practice-memory-match | uz | kelajak soati, kelajak |
| chqbt | Pre-conscription training | humanities | practice-memory-match | uz | chqbt |
| tasviriy-sanat | Fine arts | default | practice-memory-match | uz | tasviriy |
| musiqa | Music (Musiqa) | default | practice-memory-match | uz | musiqa |
| texnologiya | Technology (Texnologiya) | default | practice-memory-match | uz | texnologiya |
| chizmachilik | Technical drawing | math | practice-jigsaw | uz | chizmachilik |
| jismoniy-tarbiya | Physical education | default | practice-memory-match | uz | jismoniy tarbiya, jismoniy |

\* = existing code, preserved verbatim.

**Geografiya → humanities** is deliberate: the existing humanities flashcard block already references geography ("geography statistics with no year"); its visual policy covers maps/timelines.

## Architecture

New module **`app/services/subjects.py`** — the single source of truth:

```python
@dataclass(frozen=True)
class SubjectDef:
    code: str
    label: str       # SUBJECT_LABELS value
    family: str      # FAMILY_RULES key, or "default" -> "_default" block
    game: str        # practice-* phase (must have a _general prompt)
    language: str    # LANGUAGE_RULES key: "uz" | "english" | "russian"
    keywords: tuple[str, ...]  # folded Uzbek substrings for notion_fetch._map_subject

REGISTRY: dict[str, SubjectDef]  # insertion order = display order
SUBJECT_CODES: list[str]
def notion_keyword_pairs() -> list[tuple[str, str]]:  # (keyword, code), longest keyword first
```

Everything else **derives** from `REGISTRY` (public names unchanged, so nothing that imports them breaks):
- `flows.SUBJECTS` = `subjects.SUBJECT_CODES`; `flows.SUBJECT_GAME` = `{c: d.game}`; `flows.SUPPORTED_SUBJECTS` = `sorted(SUBJECTS)`.
- `prompts.SUBJECT_LABELS` = `{c: d.label}`; `prompts._SUBJECT_FAMILY` = `{c: d.family}`.
- `prompts.LANGUAGE_RULES` stays a text map: `{"english": _LANG_ENGLISH, "russian": _LANG_RUSSIAN, "_default": _LANG_UZBEK}`. `get_prompt` looks up by `REGISTRY[subject].language` (falling back to `_default`), preserving graceful behavior for unknown subjects.
- `notion_fetch._SUBJECT_KEYWORDS` = `subjects.notion_keyword_pairs()` (longest-first so `jismoniy tarbiya`/`axloqiy tarbiya` win over bare `tarbiya`).
- FE `web/src/lib/types.ts` (`Subject` union + `SUBJECTS` array) and `web/src/lib/subjects.ts` (`SUBJECT_LABELS` `Record<Subject,string>`) mirror the codes (short English labels). `Record<Subject,string>` forces exhaustiveness at compile time.

No import cycle: `subjects.py` is pure data (imports nothing from flows/prompts); flows/prompts/notion_fetch import it.

New language rule `_LANG_RUSSIAN`: Russian-as-L2 for native-Uzbek learners — target Russian for the learned content, formal-Uzbek scaffolding, level to the source/grade, preserve terms, translate idiomatically. No invented CEFR scale (unlike english, which has the curriculum's documented A1–B1+ grade mapping).

`books.py` needs **no change** — it validates against `SUPPORTED_SUBJECTS`, which now grows automatically.

## Verification

- Existing suite stays green: `test_prompt_coverage` / `test_general_flow` / `test_learning_flow` iterate `SUPPORTED_SUBJECTS` and now auto-cover all 30; design guarantees every subject resolves family+language+label with no leftover `{{...}}` and a game whose prompt exists.
- New `tests/services/test_subject_registry.py`:
  - Regression: the 7 legacy codes keep their exact family/game/label.
  - Every registry subject: family resolvable, game prompt-file exists, language key valid, `get_prompt` renders no `{{`.
  - `_map_subject` maps representative Uzbek titles to the right code (incl. shadowing: "Jismoniy tarbiya"→jismoniy-tarbiya not tarbiya; "Axloqiy tarbiya"→odobnoma; "Matematika"→matematika; "Algebra"→math-algebra; "Rus tili"→russian; "Jahon tarixi"→history).
  - `notion_keyword_pairs()` is sorted longest-first.
- `cd web && npx tsc -p tsconfig.app.json --noEmit` clean.
- Real CLI smoke: generate one phase (e.g. case-based-preview) for a NEW subject (e.g. `geografiya`, `russian`) via a real provider call to prove end-to-end acceptance + sane output.

## Risks

- Keyword shadowing → mitigated by longest-first ordering + explicit tests.
- `matematika` now mapped (was intentionally absent) — distinct code, not algebra; covered by test.
- FE/Python code drift → both mirror the same code list; a comment in each points at `subjects.py` as source of truth.
