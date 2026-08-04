# Structured `content_json` producer — acceptance record

**Date:** 2026-08-03 · **Lane:** `feat/content-json-producer` (worklog 0162) ·
**Plan:** `docs/superpowers/plans/shipped/2026-08-03-content-json-producer.md` ·
**Spec:** `docs/superpowers/specs/2026-08-03-content-json-output-design.md` (rev 6)

This is the acceptance evidence the plan promised and did not ship. Its absence,
together with the missing `tests/conformance/test_platform_contract.py`, is why
three defects survived to the gate: an ingest envelope with no `payload` key, a
string `grade` against `IntegerField(1..11)`, and a `--post` path that would have
posted phases the platform silently drops.

---

## 1. Accepted run (the one that passed)

| | |
|---|---|
| Scratch DB | `edu_cj_task9` (throwaway; never production) |
| Transport | `transport=api` — the transport production actually uses |
| Cost | **$0.0862** |
| Calls | **5** — 2 × `phase.run`, 2 × judge, 1 × solve |
| Tokens | **15,942 in / 9,084 out** |
| Phases | `practice-rlc`, `practice-sentence` |

Result, per phase row:

- `authoring_mode = "structured"` on both — the structured lane produced the
  output; neither fell back to markdown.
- `judge_status = "ok"` on both.
- `practice-rlc` `solver_status = "ok"` (practice-rlc is in `_SOLVER_PHASES`; the
  solver re-checks the answer key inside the *rendered markdown*, which is why the
  renderer emits an author-only `## Answer key` section at all).
- **No regeneration** — neither a judge MAJOR regen nor a solver mismatch regen.

Cross-checks on the produced artifacts:

- `content_json` validates CLEAN against the platform's live
  `validate_rlc_config` / `validate_sentence_fill_config`.
- Rendered markdown is clean through `content_lint`, survives answer-section
  stripping (`strip_answer_sections`), and loads for Notion rendering and the
  teaching audit.

## 2. The earlier run that FAILED ($0.2717)

An earlier acceptance attempt cost **$0.2717** and **failed on `major_shipped`**:
the judge returned a MAJOR verdict on shipped output. Diagnosis: a structured
phase's markdown is *derived*, and it was being graded against the hand-authored
**markdown** prompt — a category error, since that contract describes prose the
JSON author was never asked to write.

That failure produced the **artifact-aware judge** (`_judge_inputs_for`): a
structured artifact is graded on the canonical serialization of its
`content_json` against `get_structured_prompt(...)`; every markdown mode keeps
today's path and custom overrides are preserved. The $0.0862 run above is the
re-run after that fix.

## 3. What acceptance did NOT establish

The live run proves the **producer**. It says nothing about ingestion:

- `transform_chb` still reads `output_md` and ignores per-phase `content_json`.
- Nothing is `native`. Only a three-repo acceptance (generator + platform +
  mobile) may claim that.

Run through the platform's **current** markdown parsers, our rendered output gives:

| phase | parser | outcome |
|---|---|---|
| `practice-rlc` | `parse_rlc` | `outcome='downgraded'`, `fallback=True` |
| `practice-sentence` | `parse_sentence` | **`outcome='dropped'`** — "no resolvable blank + tagged choice" |

The ingest endpoint **schedules transformation immediately** — it is not passive
raw staging — so posting today would silently drop `practice-sentence` and could
carry an incomplete packet into review or publication.

**Therefore `scripts/ingest_to_platform.py --post` fails closed** on any payload
containing a `authoring_mode == "structured"` phase, unless the target platform
advertises native support for every `(phase_name, content_schema_version)` at
`GET {PLATFORM_BASE_URL}/api/v1/library/homework-imports/capabilities`. That
endpoint does not exist yet, so **every structured post is blocked today — the
intended current behaviour**. Missing, unreachable, non-200, malformed or
incomplete capability information all block. There is **no `--force`**: an
operator override is precisely the mechanism that turns "we know this drops a
phase" into "we shipped a packet missing a phase". `--dry-run` (the default) and
`--check-map` remain fully available and never probe or POST.

## 4. The standing gate

`tests/conformance/test_platform_contract.py` locks all of the above against the
platform's REAL source, read from `origin/Akademiya-AI` in the sibling checkout
(`/Users/macmini5/Documents/Class-A-Education-Platform-Backend`); the whole file
skips when that checkout is absent. It covers:

1. **Serializer contract** — the built envelope satisfies
   `HomeworkImportIngestSerializer` field-for-field. The declarations are read
   out of the real source with `ast` rather than by importing DRF, because
   importing the module pulls in `library.models.packs`,
   `library.models.curriculum` and `schools.models` — a configured Django app
   registry and a database. Types, `max_length`, `min_value`/`max_value`,
   required-ness and "no undeclared keys" are all asserted.
2. **Validators** — both configs pass the platform's own
   `validate_rlc_config` / `validate_sentence_fill_config`.
3. **Redactor** — the real `strip_answer_sections` removes our `## Answer key`
   heading, body and answer lines, and leaves the exercise intact.
4. **Current parser outcomes** — `downgraded` for RLC, `dropped` for sentence.
   These are deliberately asserted as the *known* outcomes: **the day the
   platform gains native support this test fails loudly**, and the failure
   message points at the Fix-3 export block as the thing to lift.

The platform modules are loaded from a materialized `library/` package tree in a
tmp dir (`redactor.py`, `models/validators.py`, `services/{emission,chb_common,
chb_practice}.py`) with `django.conf.settings` stubbed — `chb_practice` uses
relative imports and `emission` does `from library.redactor import ...`, so a
bare module load cannot resolve them. `library/models/__init__.py` is left empty
on purpose; the real one imports Django models.

No network, no database, no model calls: **$0**.

## Known residuals (accepted, not defects)

Recorded explicitly so nothing downstream reads these as exact.

**1. `prompt_hash` on a `markdown_fallback` row names the STRUCTURED prompt.**
`pipeline.py:1317-1324` picks the structured prompt's hash whenever the phase has
a schema and no custom override — including the runs where the structured attempt
exhausted its schema retries and markdown actually produced the output. The row
therefore attributes the output to a prompt that did not write it;
`authoring_mode="markdown_fallback"` is what distinguishes the case.

This is **provenance-only, not operational**: `prompt_hash` has exactly one
consumer, `phase_outputs.find_latest_extract` (`app/repositories/phase_outputs.py:248-284`),
which filters `phase_name == "extract"`. No content-phase resume, reuse or
regeneration path reads it. Do not describe content-phase provenance as exact.

**2. The structured prompts do not tell the model that ids must be unique.**
`prompts/_general/structured/practice-rlc.md:22` says every step, option and chip
needs an `id` and `label`; it never says *unique*. Uniqueness is enforced by the
schemas instead (`app/schemas/content_json/rlc.py:46,57,91`,
`sentence_fill.py:49`), so a duplicate is rejected, retried, and — if the retry
also collides — falls back to markdown.

Correctness is therefore safe; what suffers is the **fallback rate**. Adding the
constraint to the prompt is a cheap improvement, deferred only because a prompt
change is a generation change and wants a real smoke to accept.

## Run-level export gate

`--post` is **all-or-nothing across the run**, not per job. Every payload is
built first, then a single capability probe covers the union of structured pairs;
if any pair is unsupported, zero jobs post. Gating per job let an earlier
markdown-only job POST before a later structured job was refused, so what got
ingested depended on argument order.

The platform's ingest is idempotent on `(pack, external_key, content_hash)`
(`uniq_homework_import_idempotency`, `apps/library/models/packs.py:187`), so a run
interrupted by a **server** error mid-batch is safely re-runnable. The preflight
prevents known-unsupported partials; it is not a distributed transaction.
