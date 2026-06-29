# Multi-Language Content Output (UZ / EN / RU) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator generate a homework packet in one of three media of instruction — Uzbek (today's only behavior), English, or Russian — selected as a global Settings default with a per-launch override, defaulting to UZ so nothing existing changes.

**Architecture:** A new `output_language` axis (`uz`|`en`|`ru`) is added alongside the existing `transport`/launch-defaults machinery: a global default on `launch_defaults`, stamped onto each `batch` and `homework_job` at launch (future-launches-only), and threaded into the single prompt-resolution seam (`prompts.get_prompt`). The prompt layer swaps the `{{LANGUAGE_RULES}}` block per medium for ordinary subjects, while the two L2 *language-class* subjects (English, Russian) keep their existing Uzbek-bridged rule untouched.

**Tech Stack:** FastAPI · SQLAlchemy/asyncpg · Alembic · Pydantic · React (Vite + TanStack Query) · pytest.

## Approach & key decisions

- **Three locked decisions (user, 2026-06-29):** (1) **global default in Settings + per-launch override** — mirror the launch-defaults/transport pattern exactly; (2) **medium affects only non-L2 subjects** — the English/Russian *class* subjects (`subjects.language in {"english","russian"}`, verified exclusive to those two subjects) keep today's Uzbek-bridge L2 rule regardless of medium; (3) **extract stays language-neutral** — no extract prompt change, no cache-key change, no extra extract cost.
- **Non-breaking is provable, not asserted.** The new column defaults `server_default="uz"` on every table, the new `get_prompt(..., output_language="uz")` arg defaults to `uz`, and for `output_language="uz"` on a non-L2 subject the resolver returns the *byte-identical* `_LANG_UZBEK` block today's `_default` returns; for an L2 subject it returns the *same* `LANGUAGE_RULES[sd.language]` as today. So every existing row, every existing call, and every L2 lesson are unchanged. This is asserted by a RED-proved equivalence test in Task 1.
- **One resolution seam.** `prompts.get_prompt` is the only place language is resolved (2 callers: the pipeline generator at `pipeline.py:975` and the judge via `phase_judge.judge` → `get_prompt` at `phase_judge.py:222`). The judge must grade against the *same* contract the generator used, so `output_language` threads through `phase_judge.judge` too — otherwise an EN packet is judged against the UZ contract.
- **Batch key forks per language.** `batches` is `UNIQUE(book_id, transport)`; it becomes `UNIQUE(book_id, transport, output_language)` and `find_active_for_section`/`get_or_create_for_book` become language-scoped — else an EN launch over a UZ-generated book silently no-ops (same §9 reasoning that made `transport` part of the key).
- **Claim gate untouched.** `output_language` does not affect provider/model/auth, so `claim_next_job` and worker capabilities are not touched (the adversarially-blessed gate stays a 0-line diff).
- **Rejected:** book-level language (can't generate one book in two media without re-uploading); per-launch-only with no global default (a school would re-pick every launch); bridge-follows-medium for L2 classes (rewrites the hardcoded-Uzbek L2 prompt rules — deferred to a follow-up, logged to WISHLIST).

## Global Constraints

- `output_language` domain is exactly `{"uz", "en", "ru"}`. DB columns are `NOT NULL DEFAULT 'uz'` with a CHECK constraint over that set. The per-launch *request* field is `str | None` (None ⇒ inherit the global default).
- The new alembic revision id must be ≤ 32 chars and `down_revision = "0037_launch_defaults"` (current single head). Single head must be preserved.
- Stage only the files each task lists. Never `git add -A` (other sessions commit to `web/` and `docs/`).
- Default UZ behavior must be byte-identical to today — proven by test, not assumed.
- No new model names hardcoded; no SDK on the cli path; build spawn envs only via `agent._auth_env` (untouched here).
- FE has no JS test runner: acceptance for `web/` is `npx tsc -p tsconfig.app.json --noEmit` + `npm run build` (+ operator eyeball).

---

## File Structure

- `app/services/prompts.py` — add `MEDIUM_RULES` + `_resolve_language_rule`; add `output_language` param to `get_prompt`/`get_prompt_hash`. *(resolution seam)*
- `app/services/agent_models.py` — `OUTPUT_LANGUAGES`, `validate_output_language`, `resolve_output_language`. *(central domain + helpers)*
- `alembic/versions/0038_output_language.py` — new columns + batches unique-constraint swap. *(schema)*
- `app/models/homework_job.py`, `app/models/batch.py`, `app/models/launch_defaults.py` — ORM columns. *(schema mirror)*
- `app/repositories/launch_defaults.py` — `get`/`update` carry `output_language`. *(global default I/O)*
- `app/repositories/batches.py`, `app/repositories/jobs.py` — `create`/`get_or_create_for_book`/`find_active_for_section` thread + scope `output_language`. *(persistence plumbing)*
- `app/api/v1/batch.py`, `app/api/v1/jobs.py`, `app/api/v1/settings.py` — launch resolution + request/settings schemas. *(launch + settings layer)*
- `app/schemas/job.py` — `GenerateRequest.output_language`. *(request schema)*
- `app/services/pipeline.py`, `app/services/phase_judge.py` — thread `output_language` into generator + judge. *(runtime threading)*
- `web/src/routes/settings.tsx`, `web/src/components/fleet/launcher.tsx`, `web/src/components/.../section.tsx`, `web/src/lib/api.ts`, `web/src/lib/types.ts` — Settings row + launcher selects + client. *(FE)*
- `scripts/smoke_output_language.py` — real-call acceptance. *(gate)*

---

### Task 1: Prompt-layer medium rules + `get_prompt` resolution

**Files:**
- Modify: `app/services/prompts.py`
- Test: `tests/services/test_prompts_output_language.py` (create)

**Interfaces:**
- Produces: `get_prompt(subject, phase_name, provider_suffix="", output_language="uz") -> str`; `get_prompt_hash(subject, phase_name, output_language="uz") -> str`; `MEDIUM_RULES: dict[str,str]`; `_resolve_language_rule(subject, output_language) -> str`.

- [ ] **Step 1: Write failing tests**

```python
# tests/services/test_prompts_output_language.py
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

def test_l2_subject_ignores_medium_keeps_uzbek_bridge():
    # decision 2: english/russian CLASS subjects unchanged regardless of medium
    for lang in ("uz", "en", "ru"):
        body = prompts.get_prompt("english", "flashcards", output_language=lang)
        assert prompts.LANGUAGE_RULES["english"] in body
        assert prompts.MEDIUM_RULES["en"] not in body  # the L2 rule, not the medium rule

def test_unknown_language_falls_back_to_uz():
    subj = _uz_subject()
    body = prompts.get_prompt(subj, "flashcards", output_language="zz")
    assert prompts.MEDIUM_RULES["uz"] in body

def test_hash_differs_by_language():
    subj = _uz_subject()
    assert prompts.get_prompt_hash(subj, "flashcards", "uz") != \
           prompts.get_prompt_hash(subj, "flashcards", "en")
```

- [ ] **Step 2: Run — expect FAIL** (`get_prompt()` has no `output_language` kwarg; `MEDIUM_RULES` undefined)

Run: `uv run python -m pytest tests/services/test_prompts_output_language.py -q`
Expected: FAIL (TypeError / AttributeError).

- [ ] **Step 3: Implement.** In `app/services/prompts.py`, after the `LANGUAGE_RULES` dict (line ~66) add the two medium blocks and the map:

```python
# --- Medium-of-instruction rules (whole-output language; decision 2026-06-29) ---
# Distinct from LANGUAGE_RULES above, which are the L2 *target-language* rules for
# the English/Russian CLASS subjects. MEDIUM_RULES govern the medium of instruction
# for every OTHER subject. "uz" reuses _LANG_UZBEK so the UZ path is unchanged.
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
    "uz": _LANG_UZBEK,          # byte-identical to the legacy `_default`
    "en": _MEDIUM_ENGLISH,
    "ru": _MEDIUM_RUSSIAN,
}


def _resolve_language_rule(subject: str, output_language: str) -> str:
    """L2 language-class subjects (English/Russian) keep their own L2 rule
    regardless of the medium (decision: medium affects only other subjects).
    Every other subject renders in the chosen medium (uz/en/ru), defaulting uz."""
    sd = subjects.REGISTRY.get(subject)
    if sd and sd.language in ("english", "russian"):
        return LANGUAGE_RULES[sd.language]
    return MEDIUM_RULES.get(output_language, MEDIUM_RULES["uz"])
```

Then change `get_prompt` (line ~378) and `get_prompt_hash` (line ~397):

```python
def get_prompt(subject: str, phase_name: str, provider_suffix: str = "",
               output_language: str = "uz") -> str:
    dirname = _resolve_dir(subject, phase_name)
    body, _h = _raw(dirname, phase_name)
    body = body.replace("{{SUBJECT}}", SUBJECT_LABELS.get(subject, subject))
    body = body.replace("{{LANGUAGE_RULES}}",
                        _resolve_language_rule(subject, output_language))
    phase_blocks = FAMILY_RULES.get(phase_name, {})
    family = _SUBJECT_FAMILY.get(subject)
    family_block = phase_blocks.get(family) or phase_blocks.get("_default", "")
    body = body.replace("{{FAMILY_RULES}}", family_block)
    if provider_suffix:
        body = body + "\n\n" + provider_suffix
    return body


def get_prompt_hash(subject: str, phase_name: str, output_language: str = "uz") -> str:
    # Provenance only (recorded on agent_usages); does NOT drive cross-job reuse.
    import hashlib
    return hashlib.sha256(
        get_prompt(subject, phase_name, output_language=output_language).encode("utf-8")
    ).hexdigest()
```

> NOTE for implementer: read the *current* `get_prompt_hash` first — it returns the cached `_raw` hash. Changing it to hash the rendered body is intentional so language varies provenance. If `get_prompt_hash`'s return value is used as a reuse key anywhere, STOP and report (grep `get_prompt_hash(` — pipeline:806 records it as provenance per its own docstring). If it is provenance-only, proceed.

- [ ] **Step 4: Run — expect PASS.** `uv run python -m pytest tests/services/test_prompts_output_language.py -q`
- [ ] **Step 5: Commit** — `git add app/services/prompts.py tests/services/test_prompts_output_language.py && git commit -m "feat(prompts): medium-of-instruction rules + output_language in get_prompt"`

---

### Task 2: Central language domain + validator + resolver

**Files:**
- Modify: `app/services/agent_models.py`
- Test: `tests/services/test_output_language_validation.py` (create)

**Interfaces:**
- Produces: `OUTPUT_LANGUAGES: frozenset[str]` (`{"uz","en","ru"}`); `validate_output_language(value: str | None, *, allow_none: bool) -> str | None` (returns an error string or None — same convention as `validate_transport`); `resolve_output_language(explicit: str | None, default: str) -> str` (returns `explicit or default`).

- [ ] **Step 1: Write failing tests**

```python
# tests/services/test_output_language_validation.py
from app.services.agent_models import (
    OUTPUT_LANGUAGES, validate_output_language, resolve_output_language)

def test_domain_is_exactly_three():
    assert OUTPUT_LANGUAGES == frozenset({"uz", "en", "ru"})

def test_valid_values_pass():
    for v in ("uz", "en", "ru"):
        assert validate_output_language(v, allow_none=False) is None

def test_off_domain_returns_error():
    assert validate_output_language("fr", allow_none=False) is not None

def test_none_rejected_when_not_allowed_allowed_when_allowed():
    assert validate_output_language(None, allow_none=False) is not None
    assert validate_output_language(None, allow_none=True) is None

def test_resolve_prefers_explicit_then_default():
    assert resolve_output_language("en", "uz") == "en"
    assert resolve_output_language(None, "ru") == "ru"
```

- [ ] **Step 2: Run — expect FAIL** (ImportError).
- [ ] **Step 3: Implement** in `app/services/agent_models.py` (next to `validate_transport`):

```python
OUTPUT_LANGUAGES = frozenset({"uz", "en", "ru"})

def validate_output_language(value, *, allow_none: bool):
    if value is None:
        return None if allow_none else "output_language is required"
    if value not in OUTPUT_LANGUAGES:
        return f"output_language must be one of {sorted(OUTPUT_LANGUAGES)}; got {value!r}"
    return None

def resolve_output_language(explicit, default: str) -> str:
    return explicit or default
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git add app/services/agent_models.py tests/services/test_output_language_validation.py && git commit -m "feat(agent_models): output_language domain + validator + resolver"`

---

### Task 3: Migration + ORM columns + batches unique-constraint swap

**Files:**
- Create: `alembic/versions/0038_output_language.py`
- Modify: `app/models/homework_job.py`, `app/models/batch.py`, `app/models/launch_defaults.py`
- Test: `tests/db/test_output_language_migration.py` (create; `RUN_DB_INTEGRATION`-gated like siblings)

**Interfaces:**
- Produces: `homework_jobs.output_language`, `batches.output_language`, `launch_defaults.output_language` (all `String`, `NOT NULL`, `server_default='uz'`, CHECK ∈ domain); batches unique constraint renamed to `uq_batches_book_id_transport_output_language` over `(book_id, transport, output_language)`.

- [ ] **Step 1: Write failing DB test** (gated):

```python
# tests/db/test_output_language_migration.py
import os, pytest
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs real Postgres")

@pytest.mark.asyncio
async def test_columns_default_uz_and_constraint(db_session):  # reuse the repo conftest fixture
    from sqlalchemy import text
    for tbl in ("homework_jobs", "batches", "launch_defaults"):
        row = (await db_session.execute(text(
            "SELECT column_default, is_nullable FROM information_schema.columns "
            "WHERE table_name=:t AND column_name='output_language'"), {"t": tbl})).first()
        assert row is not None, f"{tbl}.output_language missing"
        assert "uz" in (row[0] or ""), f"{tbl} default not uz"
        assert row[1] == "NO"
    names = [r[0] for r in (await db_session.execute(text(
        "SELECT conname FROM pg_constraint WHERE conrelid='batches'::regclass "
        "AND contype='u'"))).all()]
    assert "uq_batches_book_id_transport_output_language" in names
    assert "uq_batches_book_id_transport" not in names
```

> Match the existing DB-test fixture style — read `tests/db/` (or wherever `0037` was acceptance-tested) for the exact session fixture name; adapt `db_session` accordingly.

- [ ] **Step 2: Author the migration** `alembic/versions/0038_output_language.py`:

```python
"""add output_language to jobs, batches, launch_defaults

Revision ID: 0038_output_language
Revises: 0037_launch_defaults
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_output_language"
down_revision = "0037_launch_defaults"
branch_labels = None
depends_on = None

_CK = "output_language IN ('uz','en','ru')"

def upgrade():
    for tbl in ("homework_jobs", "batches", "launch_defaults"):
        op.add_column(tbl, sa.Column(
            "output_language", sa.String(), nullable=False, server_default="uz"))
        op.create_check_constraint(f"ck_{tbl}_output_language", tbl, _CK)
    op.drop_constraint("uq_batches_book_id_transport", "batches", type_="unique")
    op.create_unique_constraint(
        "uq_batches_book_id_transport_output_language", "batches",
        ["book_id", "transport", "output_language"])

def downgrade():
    op.drop_constraint(
        "uq_batches_book_id_transport_output_language", "batches", type_="unique")
    op.create_unique_constraint(
        "uq_batches_book_id_transport", "batches", ["book_id", "transport"])
    for tbl in ("homework_jobs", "batches", "launch_defaults"):
        op.drop_constraint(f"ck_{tbl}_output_language", tbl, type_="check")
        op.drop_column(tbl, "output_language")
```

- [ ] **Step 3: Add ORM columns.** In each model add (mirroring the existing `transport` column style — keep `server_default="uz"` so ORM-created rows match DB):

```python
output_language: Mapped[str] = mapped_column(String, nullable=False, server_default="uz")
```

In `app/models/batch.py` replace the `UniqueConstraint(...)` (line 53) with:

```python
UniqueConstraint("book_id", "transport", "output_language",
                 name="uq_batches_book_id_transport_output_language"),
```

(Leave the `CheckConstraint` import in place; add a `CheckConstraint(_CK, name=...)` to each model's `__table_args__` only if the sibling columns model their CHECKs in ORM — match the existing convention in each file; the migration is the source of truth either way.)

- [ ] **Step 4: Scratch-DB acceptance** (controller runs at review):

```bash
createdb -U macmini5 edu_ml_test
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_ml_test RUN_DB_INTEGRATION=1 \
  uv run --extra dev alembic upgrade head
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_ml_test RUN_DB_INTEGRATION=1 \
  uv run --extra dev python -m pytest tests/db/test_output_language_migration.py -q
# verify down/up round-trips, then:
dropdb edu_ml_test
```
Expected: upgrade head clean (single head), test PASS.

- [ ] **Step 5: Commit** — stage the migration + 3 model files + the test, commit `feat(db): output_language columns + per-language batch key (mig 0038)`.

---

### Task 4: `launch_defaults` repo + Settings API carry `output_language`

**Files:**
- Modify: `app/repositories/launch_defaults.py`, `app/api/v1/settings.py`
- Test: `tests/api/test_settings_output_language.py` (create)

**Interfaces:**
- Consumes: `validate_output_language` (Task 2); `launch_defaults.output_language` (Task 3).
- Produces: `GET /settings/launch-defaults` returns `output_language`; `PUT` accepts + validates it (concrete, `allow_none=False`); `update()` persists it.

- [ ] **Step 1: Failing tests** — GET exposes `output_language` (seed `"uz"`); PUT `{"output_language":"en", ...}` round-trips; PUT `"fr"` → 422; PUT omitting it leaves it unchanged (or 422 if your PUT is full-replace — match the existing `0037` PUT semantics, read `settings.py` first).
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** Add `output_language` to the launch-defaults Pydantic out/in models, the `get()` projection, and `update()`'s settable fields. In the PUT handler, call `validate_output_language(body.output_language, allow_none=False)` → 422 on error (alongside the existing provider/model/transport validation).
- [ ] **Step 4: Run — expect PASS** (`uv run python -m pytest tests/api/test_settings_output_language.py -q`).
- [ ] **Step 5: Commit** — `feat(settings): global output_language default`.

---

### Task 5: Repository plumbing — `create` / `get_or_create_for_book` / `find_active_for_section`

**Files:**
- Modify: `app/repositories/batches.py`, `app/repositories/jobs.py`
- Test: `tests/repositories/test_batch_language_key.py` (create; DB-gated) + a unit test for `find_active_for_section` scoping.

**Interfaces:**
- Produces: `batches.get_or_create_for_book(..., output_language: str)` (insert value + conflict target `["book_id","transport","output_language"]`); `jobs.create(..., output_language: str)`; `jobs.find_active_for_section(..., output_language: str)` scopes its query by language.

- [ ] **Step 1: Failing tests** — (a) DB: `get_or_create_for_book` for `(book, transport=cli, output_language=uz)` then `(…, output_language=en)` returns **two distinct** batch ids (fork); same triple twice returns the **same** id. (b) `find_active_for_section` does not return a `uz` job when queried for `en`.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** Thread `output_language` through `get_or_create_for_book` (add to the `insert().values(...)` and set `index_elements=["book_id","transport","output_language"]` at `batches.py:72`); through `jobs.create` (`jobs.py:52` block); and add an `output_language ==` filter to `find_active_for_section` (`jobs.py:93`). Keep all new params keyword with no default at the repo layer (callers in Tasks 6/7 pass them explicitly) — a default would silently re-introduce the no-op-fork bug.
- [ ] **Step 4: Run — expect PASS** (DB-gated test under `RUN_DB_INTEGRATION=1` against the scratch DB; unit test always).
- [ ] **Step 5: Commit** — `feat(repos): language-scoped batch key + job create/dedup`.

---

### Task 6: Batch launch resolution + request schema

**Files:**
- Modify: `app/api/v1/batch.py`
- Test: extend `tests/api/` batch tests (find the file that builds a batch launch payload — likely `test_batch_*`).

**Interfaces:**
- Consumes: `resolve_output_language`, `validate_output_language` (Task 2); `ld.output_language` (Task 4); language-scoped repos (Task 5).
- Produces: batch request gains `output_language: str | None = None`; the resolved value is stamped on the batch row and every job it creates, and passed to `get_or_create_for_book` + `find_active_for_section`.

- [ ] **Step 1: Failing tests** — (a) launch with `output_language="en"` → created batch + jobs carry `output_language="en"`; (b) launch omitting it with global default `"ru"` → rows carry `"ru"` (inherit); (c) `output_language="fr"` → 400.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** Add the field to the batch request model (`batch.py:~41`). After the existing transport validation (line ~120) add `err = validate_output_language(body.output_language, allow_none=True)` → 400. In the resolution block (mirror lines 177–183) add:

```python
res_output_language = resolve_output_language(body.output_language, ld.output_language)
```

Pass `output_language=res_output_language` to `get_or_create_for_book`, `find_active_for_section`, and every `jobs.create(...)` in this handler; set it on the batch row.

- [ ] **Step 4: Run — expect PASS** + full file: `uv run python -m pytest tests/api/ -q -k batch`.
- [ ] **Step 5: Commit** — `feat(api): batch launch resolves + stamps output_language`.

---

### Task 7: Single-job `/generate` resolution + request schema

**Files:**
- Modify: `app/api/v1/jobs.py`, `app/schemas/job.py`
- Test: extend the single-section generate test.

**Interfaces:**
- Consumes: same helpers; the handler already fetches `ld` at `jobs.py:240`.
- Produces: `GenerateRequest.output_language: str | None = None`; the job row + its `find_active_for_section` lookup carry the resolved language.

- [ ] **Step 1: Failing tests** — single-section generate with `output_language="en"` → job row `"en"`; omitted + global `"ru"` → `"ru"`; `"xx"` → 400; the dedup/adoption lookup is language-scoped (an existing UZ job is NOT adopted for an EN request → a new job is created).
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.** Add `output_language: str | None = None` to `GenerateRequest` (`job.py:53`). In `generate` (`jobs.py:85`): validate (`allow_none=True` → 400), compute `res_output_language = resolve_output_language(body.output_language, ld.output_language)` using the `ld` already loaded at :240, pass it to `find_active_for_section` and `jobs.create`.
- [ ] **Step 4: Run — expect PASS** + `uv run python -m pytest tests/api/ -q -k generate`.
- [ ] **Step 5: Commit** — `feat(api): single-job generate resolves + stamps output_language`.

---

### Task 8: Pipeline + judge threading

**Files:**
- Modify: `app/services/pipeline.py`, `app/services/phase_judge.py`
- Test: `tests/services/test_pipeline_output_language.py` (create) + extend a judge test.

**Interfaces:**
- Consumes: `job.output_language`; `get_prompt(..., output_language=)` (Task 1).
- Produces: generator renders the contract in the job's medium; `phase_judge.judge(..., output_language: str = "uz")` grades against the *same* medium contract.

- [ ] **Step 1: Failing tests** — (a) a fake job with `output_language="en"`: the prompt built at the generator seam contains `MEDIUM_RULES["en"]` (assert on the string passed to `get_prompt`, or capture the built prompt). (b) `phase_judge.judge` called with `output_language="en"` and a non-L2 subject builds its contract with the EN medium block (monkeypatch `agent.run_phase` to capture the contract; assert `MEDIUM_RULES["en"]` present). Make this test BITE: it must fail if `output_language` is dropped before `get_prompt`.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement.**
  - `pipeline.py:975`: `get_prompt(subject, phase_name, output_language=job.output_language)`.
  - `pipeline.py:806`: `get_prompt_hash(subject, phase_name, output_language=job.output_language)`.
  - `phase_judge.judge(...)`: add `output_language: str = "uz"` param; line ~222 → `contract = contract_override or get_prompt(subject, phase_name, output_language=output_language)`.
  - In `pipeline.py` where the judge kwargs are built (around :1028/:1088, the `_run_judge` / `phase_judge.judge(**kwargs)` path at :756), add `output_language=job.output_language` to the kwargs. Verify the custom-prompt-override path (`contract_override`) is unaffected (custom prompts are language-as-authored).
- [ ] **Step 4: Run — expect PASS** + `uv run python -m pytest tests/services/ -q -k "pipeline or judge"`.
- [ ] **Step 5: Commit** — `feat(pipeline): thread output_language into generator + judge`.

---

### Task 9: Frontend — Settings row + launcher selects + client

**Files:**
- Modify: `web/src/routes/settings.tsx`, `web/src/components/fleet/launcher.tsx`, the section launcher component, `web/src/lib/api.ts`, `web/src/lib/types.ts`
- Acceptance: `npx tsc -p tsconfig.app.json --noEmit` + `npm run build`

**Interfaces:**
- Settings: an Output Language `Select` (UZ / EN / RU) bound to the launch-defaults `output_language`, persisted via the existing PUT.
- Launchers: an Output Language `Select` defaulting to inherit (value `undefined`/null), rendered like the role controls as `Auto → <global default>`; the value (only when explicitly chosen) is added to the launch body. Match `section.tsx`'s inherit convention so single-section launches behave like Fleet.

- [ ] **Step 1:** Add `output_language` to the launch-defaults + launch-body TS types in `types.ts`; extend the api client (`api.ts`) settings GET/PUT + the two launch calls to carry it.
- [ ] **Step 2:** Settings page: add the row (3-option select; no "Auto" — the global default is concrete, matching the provider/model selects shipped in #53).
- [ ] **Step 3:** Fleet + Section launchers: add the inherit-defaulted select; show the resolved global as the Auto hint (reuse the `resolvedDefault` display pattern from the role controls).
- [ ] **Step 4:** `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build` → clean.
- [ ] **Step 5: Commit** — stage only the listed `web/` files (never `package-lock.json`); `feat(web): output language in Settings + launchers`.

---

### Task 10: Real-call acceptance smoke

**Files:**
- Create: `scripts/smoke_output_language.py`

**Goal (CLAUDE.md acceptance gate):** prove the medium switch actually changes generated language, that UZ is unchanged, and that an L2 class is unaffected.

- [ ] **Step 1:** Script (module form `-m scripts.smoke_output_language`) that calls `agent.run_phase_prompt` in-process (no server) for a non-L2 subject (e.g. `matematika`, `flashcards`) three times — `output_language` `uz`/`en`/`ru` — building the prompt via `get_prompt`, plus once for subject `english` under `output_language="ru"`. Print a short verdict per run.
- [ ] **Step 2:** Controller runs it with a real provider (gemini or claude CLI, whichever authenticates headless — claude CLI + dummy `DATABASE_URL` per the repo's smoke convention). Assert by eye + a cheap heuristic: EN run is predominantly Latin/English, RU run contains Cyrillic, UZ run matches today, and the `english`-subject/`ru` run is still Uzbek-bridged (no Cyrillic scaffolding). One phase per run — minimal tokens; this is the sanctioned real call, NOT mass generation.
- [ ] **Step 3: Commit** — `test(smoke): output_language real-call acceptance`.

---

### Task 11: Docs de-stale + worklog + finish

**Files:** `README.md`, `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`, `docs/DATABASE.md`, `docs/memory/MASTER_MEMORY.md`, `docs/memory/INDEX.md`, `docs/memory/ROADMAP.md` / `WISHLIST.md`, and `git mv` this plan into `docs/superpowers/plans/shipped/`.

- [ ] **Step 1: Rebase-check first** — `git fetch origin && git log HEAD..origin/Nggaev-v2`. If the base moved, rebase onto `origin/Nggaev-v2`, resolve, re-run the suite.
- [ ] **Step 2:** Document the `output_language` axis: the medium-vs-L2-target distinction, the three columns + per-language batch key in `DATABASE.md`, the Settings/launcher controls, and the `get_prompt` seam in `CODE_MAP.md`.
- [ ] **Step 3:** Worklog entry in `MASTER_MEMORY.md` + an `INDEX.md` row (verify the next-free worklog number against the live `MASTER_MEMORY.md` tip at finish — do not trust a branch-time number). Log the deferred "bridge-follows-medium for L2 classes" follow-up to `WISHLIST.md`.
- [ ] **Step 4:** `git mv docs/superpowers/plans/2026-06-29-multi-language-output.md docs/superpowers/plans/shipped/`.
- [ ] **Step 5:** Full suite green: `uv run python -m pytest tests/ -q` (+ the DB-gated subset against the scratch DB). Then `finishing-a-development-branch` → push the branch, open the PR routed to the gatekeeper (no self-merge).
- [ ] **Step 6: Commit** — `docs: output_language worklog + reference de-stale`.

---

## Self-review notes (controller)

- **Spec coverage:** decisions 1–3 map to Tasks 4+6+7 (global+override), Task 1 (`_resolve_language_rule` L2 guard), and the explicit "no extract change" (extract is never touched). ✓
- **Type consistency:** `output_language` is `str` everywhere; request fields are `str | None` (None=inherit); repo params are keyword-required (no default) to prevent silent no-op forks; `get_prompt`/`judge` default `"uz"`. ✓
- **Non-breaking proof** lives in Task 1's `test_uz_default_is_byte_identical...` and `test_l2_subject_ignores_medium...` — these must RED-prove (fail if the resolver is reverted). ✓
- **Open verify-at-execution items** (flagged inline, not placeholders): exact DB-test fixture name (Task 3), the Settings PUT replace-vs-merge semantics (Task 4), the exact section-launcher component path + judge-kwargs construction site (Tasks 8–9). Each implementer reads the real file first.
