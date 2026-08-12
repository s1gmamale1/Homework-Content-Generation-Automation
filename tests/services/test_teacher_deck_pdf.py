"""Task 3 — render_teacher_deck_pdf: lazy-WeasyPrint HTML→PDF renderer.

`app/services/teacher_deck.py` runs inside `notion_archive.py` on EVERY
worker, including Windows boxes without WeasyPrint's native libs (pango/
cairo/...). `import weasyprint` MUST stay inside `render_teacher_deck_pdf`'s
function body so a missing native lib fails at call time (caller degrades),
never at module import time (which would crash the whole pipeline
fleet-wide). See the module-level docstring in teacher_deck.py.

Two tests:
  1. A real-PDF test, guarded so the suite stays green on hosts (like this
     dev Mac) without the native libs. It runs for real in Task 8 on a
     pango-equipped host. NOTE: on this WeasyPrint version, `import
     weasyprint` itself eagerly dlopen()s pango and raises **OSError** (not
     ImportError) when the native lib is entirely absent — so
     `pytest.importorskip`, which only catches ImportError, doesn't skip
     cleanly by itself here. The guard below wraps it and skips on either
     exception type, matching the "ImportError/OSError propagates" wording
     used for the missing-lib case throughout this module and the brief.
  2. An unguarded import-safety test that proves (a) importing
     `app.services.teacher_deck` never requires weasyprint, and (b)
     `render_teacher_deck_pdf` propagates a missing-lib error rather than
     swallowing it or returning silently.
"""
import importlib
import json

import pytest

from app.schemas.content_json import TeacherDeck
from app.services.teacher_deck import render_teacher_deck_pdf

FIXTURE_PATH = "tests/fixtures/teacher_deck/hindiston_topic19.json"


def _deck() -> TeacherDeck:
    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        return TeacherDeck.model_validate(json.load(fh))


def test_render_teacher_deck_pdf_produces_real_pdf_bytes():
    try:
        pytest.importorskip("weasyprint")  # skips cleanly on plain ImportError
    except OSError as exc:
        # This WeasyPrint version raises OSError (not ImportError) at import
        # time when pango is entirely absent — see module docstring.
        pytest.skip(f"weasyprint native libs unavailable: {exc}")
    deck = _deck()
    out = render_teacher_deck_pdf(deck)
    assert isinstance(out, bytes)
    assert out.startswith(b"%PDF-")
    assert len(out) > 1000


def test_module_imports_without_weasyprint_and_pdf_fn_propagates_missing_lib(monkeypatch):
    import builtins

    import app.services.teacher_deck as td

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "weasyprint" or name.startswith("weasyprint."):
            raise ImportError("simulated: weasyprint native libs unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    # Reloading with weasyprint blocked proves the module body itself never
    # imports weasyprint at top level.
    importlib.reload(td)

    # render_teacher_deck_markdown (pure markdown, no weasyprint) still works.
    deck = _deck()
    assert td.render_teacher_deck_markdown(deck)

    # render_teacher_deck_pdf must propagate the import/lib error, not
    # swallow it. Real hosts may raise either ImportError (module missing)
    # or OSError (module present, native lib missing) — both are the
    # documented "propagates, not swallowed" contract.
    with pytest.raises((ImportError, OSError)):
        td.render_teacher_deck_pdf(deck)

    # Restore the module to its normal (unblocked) state for other tests.
    monkeypatch.undo()
    importlib.reload(td)
