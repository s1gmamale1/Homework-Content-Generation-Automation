"""Cross-repo conformance gate: what we ship vs what the platform actually accepts.

This is the gate whose absence let three defects through — a `payload`-less ingest
envelope (400 on every post), a string `grade` against `IntegerField(1..11)`, and a
`--post` path that would have shipped a phase the platform silently drops. Unit
tests could not catch any of them, because all three are claims about ANOTHER repo.

Everything here reads the platform's REAL source from
``origin/Akademiya-AI`` in the sibling checkout — no vendored copies, no
hand-transcribed field lists — so the day the platform changes, this fails.

Nothing here talks to a network, a database or a model.

**Where the platform source comes from** is configurable, because a gate that
only runs on one laptop is the gate that let those three defects through:

- ``PLATFORM_SRC``  — path to the platform checkout. Defaults to the sibling
  directory on the authoring machine.
- ``PLATFORM_REF``  — git ref to read (default ``origin/Akademiya-AI``). The
  literal ``WORKTREE`` reads the files off disk instead, which is what CI wants:
  the job must gate the platform revision *under review*, not whatever
  ``origin/Akademiya-AI`` happened to be when the runner cloned.
- ``REQUIRE_PLATFORM_CONTRACT=1`` — **turns every skip in this file into a hard
  failure.** Without it the suite skips when the platform is absent, which is
  right for a laptop and catastrophic for CI: a silently-skipped cross-repo gate
  reports green while gating nothing. The platform repo's CI sets it.
"""
from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys
import types

import pytest

from app.schemas.content_json import RlcConfig, SentenceFillConfig
from app.services import phase_render
from app.services.platform_payload import build_ingest_payload

_DEFAULT_ROOT = "/Users/macmini5/Documents/Class-A-Education-Platform-Backend"

PLATFORM_ROOT = pathlib.Path(os.environ.get("PLATFORM_SRC") or _DEFAULT_ROOT)
PLATFORM_REF = os.environ.get("PLATFORM_REF") or "origin/Akademiya-AI"
REQUIRED = os.environ.get("REQUIRE_PLATFORM_CONTRACT") == "1"


def _unavailable(reason: str):
    """Skip locally, FAIL when the caller declared this gate mandatory."""
    if REQUIRED:
        pytest.fail(
            f"REQUIRE_PLATFORM_CONTRACT=1 but {reason}. This gate was asked to "
            "run; skipping it would report green while checking nothing."
        )
    pytest.skip(reason)


# A missing checkout under REQUIRE_PLATFORM_CONTRACT is a COLLECTION error on
# purpose — louder than a failing test, and impossible to mistake for a pass.
if REQUIRED and not PLATFORM_ROOT.exists():
    raise RuntimeError(
        f"REQUIRE_PLATFORM_CONTRACT=1 but no platform checkout at {PLATFORM_ROOT}. "
        "Set PLATFORM_SRC to the platform repo root."
    )

pytestmark = pytest.mark.skipif(
    not PLATFORM_ROOT.exists(),
    reason=f"platform checkout absent at {PLATFORM_ROOT}",
)

# The platform files this gate loads. `emission` does `from library.redactor import
# strip_answer_sections` and `chb_practice` does `from .chb_common import ...`, so
# the relative/absolute imports only resolve inside a real `library` package tree —
# hence the materialized package below rather than a bare module load.
_PLATFORM_FILES = {
    "library/__init__.py": None,
    "library/redactor.py": "apps/library/redactor.py",
    "library/models/__init__.py": None,
    "library/models/validators.py": "apps/library/models/validators.py",
    "library/services/__init__.py": None,
    "library/services/emission.py": "apps/library/services/emission.py",
    "library/services/chb_common.py": "apps/library/services/chb_common.py",
    "library/services/chb_practice.py": "apps/library/services/chb_practice.py",
}

_SERIALIZER_PATH = "apps/library/serializers/homework_imports.py"


def _show(repo_path: str) -> str:
    """Read a platform file at ``PLATFORM_REF``, or skip/fail if unreadable.

    ``PLATFORM_REF=WORKTREE`` reads the checked-out file instead of a git ref.
    CI uses it so the gate describes the revision under review; a laptop uses a
    ref so an unrelated dirty tree cannot turn the gate red.
    """
    if PLATFORM_REF == "WORKTREE":
        path = PLATFORM_ROOT / repo_path
        if not path.is_file():
            _unavailable(f"no such file in the platform worktree: {path}")
        return path.read_text(encoding="utf-8")

    proc = subprocess.run(
        ["git", "-C", str(PLATFORM_ROOT), "show", f"{PLATFORM_REF}:{repo_path}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        _unavailable(f"cannot read {PLATFORM_REF}:{repo_path} — {proc.stderr.strip()}")
    return proc.stdout


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    """The platform's real redactor / validators / parsers, importable.

    `redactor` imports `django.conf.settings` (only for `SECRET_KEY` inside
    `stable_shuffle`), so django is stubbed rather than configured — a full
    Django setup would need the platform's settings module and a database.
    `library/models/__init__.py` is deliberately EMPTY here: the real one imports
    Django models, and this gate only needs `validators.py` beside it.
    """
    root = tmp_path_factory.mktemp("platpkg")
    for rel, repo_path in _PLATFORM_FILES.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("" if repo_path is None else _show(repo_path), encoding="utf-8")

    django = types.ModuleType("django")
    conf = types.ModuleType("django.conf")
    conf.settings = types.SimpleNamespace(SECRET_KEY="conformance-stub")
    django.conf = conf
    added = [m for m in ("django", "django.conf") if m not in sys.modules]
    sys.modules.setdefault("django", django)
    sys.modules.setdefault("django.conf", conf)
    sys.path.insert(0, str(root))
    try:
        from library.models.validators import (  # noqa: PLC0415
            validate_rlc_config, validate_sentence_fill_config,
        )
        from library.redactor import strip_answer_sections  # noqa: PLC0415
        from library.services.chb_practice import parse_rlc, parse_sentence  # noqa: PLC0415

        yield types.SimpleNamespace(
            validate_rlc_config=validate_rlc_config,
            validate_sentence_fill_config=validate_sentence_fill_config,
            strip_answer_sections=strip_answer_sections,
            parse_rlc=parse_rlc,
            parse_sentence=parse_sentence,
        )
    finally:
        sys.path.remove(str(root))
        for name in list(sys.modules):
            if name == "library" or name.startswith("library."):
                del sys.modules[name]
        for name in added:
            sys.modules.pop(name, None)


# --- our side ---------------------------------------------------------------

def _rlc_cfg() -> RlcConfig:
    def opts():
        return [{"id": "o0", "label": "Yes", "is_correct": True},
                {"id": "o1", "label": "No", "is_correct": False}]
    return RlcConfig.model_validate({
        "id": "c1", "title": "Fire audit", "intro": "You inspect a hall.",
        "expert_role": "fire_inspector",
        "steps": [
            {"id": "s1", "kind": "decision", "title": "Choose",
             "prompt": "Evacuate?", "options": opts()},
            {"id": "s2", "kind": "info_request", "title": "Ask",
             "prompt": "What data?", "options": opts()},
            {"id": "s3", "kind": "final_decision", "title": "Decide",
             "prompt": "Final?", "options": opts()},
            {"id": "s4", "kind": "concept_select", "title": "Concept", "prompt": "Which?",
             "concept_chips": [{"id": "k1", "label": "Load", "is_correct": True},
                               {"id": "k2", "label": "Colour", "is_correct": False}]},
            {"id": "s5", "kind": "reasoning", "title": "Explain",
             "prompt": "Why?", "min_chars": 80},
        ],
    })


def _sentence_cfg() -> SentenceFillConfig:
    return SentenceFillConfig.model_validate({"items": [{
        "id": "i1", "mode": "word_bank",
        "passage": "A ___ ran past the ___.",
        "answers": ["cat", "gate"], "word_bank": ["cat", "gate", "dog"],
    }]})


def _envelope() -> dict:
    rlc, sf = _rlc_cfg(), _sentence_cfg()
    job = {
        "id": "11111111-1111-1111-1111-111111111111",
        "book_id": "22222222-2222-2222-2222-222222222222",
        "subject": "history", "grade": 8, "output_language": "uz",
    }
    phases = [
        {"phase_name": "extract", "output_md": "notes", "status": "done"},
        {"phase_name": "practice-rlc", "status": "done",
         "output_md": phase_render.render_md("practice-rlc", rlc),
         "content_json": rlc.model_dump(mode="json"),
         "content_schema_version": RlcConfig.SCHEMA_VERSION,
         "authoring_mode": "structured", "judge_status": "ok"},
        {"phase_name": "practice-sentence", "status": "done",
         "output_md": phase_render.render_md("practice-sentence", sf),
         "content_json": sf.model_dump(mode="json"),
         "content_schema_version": SentenceFillConfig.SCHEMA_VERSION,
         "authoring_mode": "structured", "judge_status": "ok"},
    ]
    return build_ingest_payload(job=job, phases=phases, subject_map={"history": 7})


# --- 1. serializer contract -------------------------------------------------

def _declared_fields() -> dict[str, tuple[str, dict]]:
    """(field_name -> (drf_field_class, kwargs)) parsed from the REAL serializer.

    DRF is not installed here and importing the module would pull in
    `library.models.packs`, `library.models.curriculum` and `schools.models` —
    i.e. a configured Django app registry and a database. The declarations are
    plain class-body assignments, so the source itself is the contract and the
    AST is a faithful, dependency-free reading of it.
    """
    tree = ast.parse(_show(_SERIALIZER_PATH))
    cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "HomeworkImportIngestSerializer"
    )
    out: dict[str, tuple[str, dict]] = {}
    for node in cls.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        name = node.targets[0].id
        kwargs = {}
        for kw in node.value.keywords:
            try:
                kwargs[kw.arg] = ast.literal_eval(kw.value)
            except ValueError:
                kwargs[kw.arg] = ast.unparse(kw.value)
        out[name] = (node.value.func.id, kwargs)
    return out


def test_envelope_satisfies_the_ingest_serializer_field_for_field():
    fields = _declared_fields()
    env = _envelope()

    # Sanity: the parse actually found the contract we think it did.
    assert set(fields) == {
        "source", "source_ref", "language", "subject_id",
        "grade", "pack_name", "external_key", "payload",
    }

    for name, (kind, kwargs) in fields.items():
        required = kwargs.get("required", True)
        if name not in env:
            assert not required, f"required serializer field '{name}' missing from envelope"
            continue
        value = env[name]
        if kind in ("CharField", "ChoiceField"):
            assert isinstance(value, str), f"{name} must be a str, got {type(value).__name__}"
            assert value != "" or kwargs.get("allow_blank"), f"{name} must not be blank"
            max_length = kwargs.get("max_length")
            if max_length is not None:
                assert len(value) <= max_length, f"{name} exceeds max_length={max_length}"
        elif kind == "IntegerField":
            # bool is a subclass of int and DRF would coerce True -> 1.
            assert isinstance(value, int) and not isinstance(value, bool), (
                f"{name} must be an int, got {type(value).__name__} ({value!r})"
            )
            if "min_value" in kwargs:
                assert value >= kwargs["min_value"]
            if "max_value" in kwargs:
                assert value <= kwargs["max_value"]
        elif kind == "JSONField":
            assert isinstance(value, dict) and value, (
                f"{name} must be a non-empty dict — validate_payload rejects "
                f"scalars, arrays and {{}}"
            )
        else:  # pragma: no cover - a new field kind means the contract moved
            pytest.fail(f"unhandled serializer field kind {kind} for '{name}'")

    # No stowaways: DRF drops unknown top-level keys silently, so a key here that
    # the serializer does not declare is data we THINK we are sending and are not.
    assert set(env) <= set(fields), f"undeclared envelope keys: {set(env) - set(fields)}"


def test_phase_rows_live_under_payload_where_the_view_reads_them():
    """The view does `payload.get("phases")` — top-level `phases` is dropped."""
    env = _envelope()
    assert "phases" not in env
    rows = env["payload"]["phases"]
    assert isinstance(rows, list)
    assert [r["phase_name"] for r in rows] == ["practice-rlc", "practice-sentence"]


# --- 2. validators ----------------------------------------------------------

def test_rlc_config_passes_the_platform_validator(platform):
    errors: dict = {}
    platform.validate_rlc_config(_rlc_cfg().model_dump(mode="json"), errors)
    assert errors == {}


def test_sentence_fill_config_passes_the_platform_validator(platform):
    errors: dict = {}
    platform.validate_sentence_fill_config(_sentence_cfg().model_dump(mode="json"), errors)
    assert errors == {}


def test_word_bank_membership_agrees_with_the_platform(platform):
    """Our membership rule must be at least as strict as the platform's.

    The platform does plain `a in bank`. We briefly did it on normalized values,
    which accepted `answers=["Cat"]` against `word_bank=["cat", ...]` — a config
    we called valid and the platform rejects at publish. A same-repo unit test
    cannot catch that class of divergence; only this one can, so the case is
    pinned HERE against the platform's real validator rather than only in
    tests/schemas.
    """
    divergent = {"items": [{
        "id": "i1", "mode": "word_bank", "passage": "A ___ ran.",
        "answers": ["Cat"], "word_bank": ["cat", "dog"],
    }]}

    # The platform rejects it...
    errors: dict = {}
    platform.validate_sentence_fill_config(divergent, errors)
    assert errors != {}, (
        "the platform now accepts case-differing bank membership — if that is "
        "deliberate, our schema may relax to match; until then it must not."
    )

    # ...so we must too, before it can ever be built.
    with pytest.raises(Exception):
        SentenceFillConfig.model_validate(divergent)


# --- 3. redactor ------------------------------------------------------------

@pytest.mark.parametrize("phase,cfg_fn", [
    ("practice-rlc", _rlc_cfg), ("practice-sentence", _sentence_cfg),
])
def test_answer_key_section_is_stripped_by_the_real_redactor(platform, phase, cfg_fn):
    """`## Answer key` exists for the solver and the judge; the student must never
    see it. `strip_answer_sections` skips from the heading to the next heading of
    equal-or-higher level — ours is last, so it swallows to EOF."""
    md = phase_render.render_md(phase, cfg_fn())
    assert "## Answer key" in md
    stripped, dropped = platform.strip_answer_sections(md)
    assert [d.lower() for d in dropped] == ["answer key"]
    assert "Answer key" not in stripped
    # The exercise itself survives.
    assert stripped.strip()
    assert md.splitlines()[0] in stripped


def test_rlc_answer_key_labels_are_gone_after_stripping(platform):
    """Not just the heading: the correct-option labels must not survive either."""
    cfg = _rlc_cfg()
    md = phase_render.render_md("practice-rlc", cfg)
    stripped, _dropped = platform.strip_answer_sections(md)
    # "1. Yes" etc. are the answer-key lines; the option list keeps "- Yes".
    for n in range(1, 6):
        assert f"\n{n}. " not in stripped


# --- 4. current parser outcomes (the Fix-3 tripwire) ------------------------

_LIFT_HINT = (
    "\n\nIf this assertion FAILS, the platform's markdown parsers have changed. "
    "Re-verify the Fix-3 export block in scripts/ingest_to_platform.py: once the "
    "platform ingests these phases natively (and advertises it at "
    "/api/v1/library/homework-imports/capabilities), the block lifts by itself — "
    "but this expectation, and the acceptance doc, must be updated to match."
)


def test_current_parser_downgrades_our_rlc_markdown(platform):
    result = platform.parse_rlc(phase_render.render_md("practice-rlc", _rlc_cfg()), None)
    assert result.outcome == "downgraded", (
        f"expected 'downgraded', got {result.outcome!r}.{_LIFT_HINT}"
    )
    assert result.fallback is True


def test_current_parser_drops_our_sentence_markdown(platform):
    result = platform.parse_sentence(
        phase_render.render_md("practice-sentence", _sentence_cfg()), None
    )
    assert result.outcome == "dropped", (
        f"expected 'dropped', got {result.outcome!r}.{_LIFT_HINT}"
    )
    # A DROPPED phase is why --post fails closed: the packet would ship without it.
    assert "no resolvable blank" in (result.notes or "")
