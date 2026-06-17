# All-Subjects Registry — Implementation Plan

> **For agentic workers:** TDD per task, commit per task. Controller re-runs tests after every commit.

**Goal:** One subject registry (`app/services/subjects.py`) that makes the supported curriculum subjects first-class, with flows/prompts/notion_fetch/FE deriving from it. No regression for the 7 existing subjects. (Surveyed 30 curriculum codes; shipped **26** — academic subjects + textbook-bearing non-exam ones; excluded PE + 3 textbook-less soft subjects after a Notion textbook audit.)

**Spec:** `docs/superpowers/specs/2026-06-16-all-subjects-registry-design.md`

**Commands:** backend `uv run python -m pytest tests/ -q`; FE typecheck `cd web && npx tsc -p tsconfig.app.json --noEmit`.

---

### Task 1: `app/services/subjects.py` registry + tests

**Files:** Create `app/services/subjects.py`; Create `tests/services/test_subject_registry.py`.

- [ ] Write `test_subject_registry.py`:
  - 7 legacy codes present with exact `(family, game, label)` (regression dict).
  - every `SubjectDef`: `family in {sciences,math,languages,humanities,default}`, `game in {practice-memory-match,practice-tictactoe,practice-jigsaw,practice-sentence}`, `language in {uz,english,russian}`, `code` is non-empty kebab, `keywords` non-empty & folded (lowercase, no apostrophes).
  - `notion_keyword_pairs()` sorted by descending keyword length; contains a pair for every code's keywords.
  - shadowing guard: in the ordered pairs, `jismoniy tarbiya` and `axloqiy tarbiya` appear before bare `tarbiya`.
- [ ] Implement `subjects.py`: `SubjectDef` frozen dataclass; `REGISTRY` dict (insertion order = spec table order); `SUBJECT_CODES = list(REGISTRY)`; `notion_keyword_pairs()` returning `(keyword, code)` sorted `key=lambda kc: -len(kc[0])`.
- [ ] Run: `uv run python -m pytest tests/services/test_subject_registry.py -q` → PASS.
- [ ] Commit: `feat(subjects): single subject registry for all curriculum subjects`.

### Task 2: Derive `flows.py` from the registry

**Files:** Modify `app/services/flows.py:14-45`.

- [ ] Replace literal `SUBJECTS` / `SUBJECT_GAME` with `from app.services import subjects` then `SUBJECTS = subjects.SUBJECT_CODES`, `SUBJECT_GAME = {c: d.game for c,d in subjects.REGISTRY.items()}`. Keep `_BASE_PHASES`, `flow_for`, `SUPPORTED_SUBJECTS`, `PHASE_DEPS`, helpers unchanged.
- [ ] Run: `uv run python -m pytest tests/services/test_general_flow.py tests/services/test_learning_flow.py -q` → PASS (now iterate all 26).
- [ ] Commit: `refactor(flows): derive SUBJECTS/SUBJECT_GAME from registry`.

### Task 3: Derive `prompts.py` from the registry + Russian L2 rule

**Files:** Modify `app/services/prompts.py:10-52, 357-371`.

- [ ] Add `_LANG_RUSSIAN` (Russian-as-L2, Uzbek scaffolding, no invented CEFR). `LANGUAGE_RULES = {"english": _LANG_ENGLISH, "russian": _LANG_RUSSIAN, "_default": _LANG_UZBEK}`.
- [ ] Replace literal `SUBJECT_LABELS` and `_SUBJECT_FAMILY` with derivations from `subjects.REGISTRY` (`{c: d.label}`, `{c: d.family}`).
- [ ] In `get_prompt`: language lookup becomes `lang_key = subjects.REGISTRY[subject].language if subject in subjects.REGISTRY else None; body.replace("{{LANGUAGE_RULES}}", LANGUAGE_RULES.get(lang_key, LANGUAGE_RULES["_default"]))`. `{{SUBJECT}}` and `{{FAMILY_RULES}}` logic unchanged (still via `SUBJECT_LABELS.get(...,subject)` and `_SUBJECT_FAMILY.get(subject)` → `_default`). Keep `family == "default"` resolving to `_default` block (it already does: `phase_blocks.get("default")` → None → `or _default`).
- [ ] Run: `uv run python -m pytest tests/services/test_prompt_coverage.py tests/services/test_subject_registry.py -q` → PASS.
- [ ] Commit: `refactor(prompts): derive labels/family from registry; add Russian L2 rule`.

### Task 4: Wire `notion_fetch` keywords from the registry + map tests

**Files:** Modify `app/services/notion_fetch.py:13-25`; extend `tests/services/test_subject_registry.py` (or new `test_notion_subject_map.py`).

- [ ] Add tests for `notion_fetch._map_subject`: "Fizika"→physics, "Biologiya"→biology, "Ona tili"→ona-tili, "Rus tili"→russian, "Ingliz tili"→english, "Matematika"→matematika, "Algebra"→math-algebra, "Geometriya"→geometriya-g7-11, "Jahon tarixi"→history, "O‘zbekiston tarixi"→history, "Axloqiy tarbiya"→odobnoma, "Jismoniy tarbiya"→jismoniy-tarbiya, "Tarbiya"→tarbiya, "Geografiya"→geografiya, "Informatika / Dasturlash"→informatika, "Tasviriy san'at"→tasviriy-sanat. A clearly-unmapped title (e.g. "Rules")→None.
- [ ] Replace literal `_SUBJECT_KEYWORDS` with `from app.services import subjects` then `_SUBJECT_KEYWORDS = subjects.notion_keyword_pairs()`. Keep `_fold`, `_map_subject`, the rest unchanged.
- [ ] Run: `uv run python -m pytest tests/services/ -q -k "subject or notion or flow or prompt"` → PASS.
- [ ] Commit: `refactor(notion): map Notion subject titles via registry keywords`.

### Task 5: FE mirror (subagent — mechanical)

**Files:** Modify `web/src/lib/types.ts:3-20`; `web/src/lib/subjects.ts:9-17`.

- [ ] `Subject` union + `SUBJECTS` array = all 26 codes (existing 7 first, then new), with a comment: source of truth is `app/services/subjects.py`.
- [ ] `SUBJECT_LABELS: Record<Subject,string>` short English labels for all 26 (e.g. matematika:"Mathematics", ona-tili:"Uzbek", adabiyot:"Literature", russian:"Russian", geografiya:"Geography", informatika:"Informatics", huquq:"Law", iqtisodiyot:"Economics", astronomiya:"Astronomy", `chizmachilik`:"Technical drawing", etc.).
- [ ] Run: `cd web && npx tsc -p tsconfig.app.json --noEmit` → clean.
- [ ] Commit: `feat(web): list all curriculum subjects in the subject registry`.

### Task 6: Full verification + real CLI smoke

- [ ] `uv run python -m pytest tests/ -q` → full suite green (real-DB tests skip without `RUN_DB_INTEGRATION=1`).
- [ ] `cd web && npx tsc -p tsconfig.app.json --noEmit` → clean.
- [ ] Real CLI smoke: in-process `get_prompt("geografiya","case-based-preview")` + one real provider call (gemini/claude) on a small lesson context for a NEW subject (geografiya and russian) — confirm acceptance + coherent markdown. No server needed.

### Task 7: Finish

- [ ] Worklog entry in `docs/memory/MASTER_MEMORY.md` + row in `docs/memory/INDEX.md`; close P1 item in `docs/memory/ROADMAP.md` if tracked.
- [ ] De-stale live-system docs that assert the subject set (grep `README.md`, `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`, `CLAUDE.md` for the 7-subject list / `flows.SUBJECTS`).
- [ ] `git mv` spec + plan into `docs/superpowers/specs/shipped/` and `plans/shipped/`.
- [ ] Push branch, open PR.
