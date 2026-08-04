"""gemini-2.5 was retired 2026-08-03 (404s on the plain API key) — these tests
prove the teaching_audit module's runnable defaults were moved off it, so a
plain `uv run python scripts/teaching_audit.py --job <id>` doesn't 404 the
moment it runs post-cutover. See agent_models.RETIRED_GEMINI_MODELS.
"""
import inspect

from app.services import teaching_audit as ta
from app.services.agent_models import RETIRED_GEMINI_MODELS


def _default(func, param: str) -> str:
    return inspect.signature(func).parameters[param].default


def test_audit_job_examiner_and_student_defaults_are_not_retired():
    examiner_default = _default(ta.audit_job, "examiner_model")
    student_default = _default(ta.audit_job, "student_model")
    assert examiner_default not in RETIRED_GEMINI_MODELS, (
        f"audit_job examiner_model default {examiner_default!r} is a retired gemini-2.5 model"
    )
    assert student_default not in RETIRED_GEMINI_MODELS, (
        f"audit_job student_model default {student_default!r} is a retired gemini-2.5 model"
    )


def test_paired_audit_examiner_and_student_defaults_are_not_retired():
    examiner_default = _default(ta.paired_audit, "examiner_model")
    student_default = _default(ta.paired_audit, "student_model")
    assert examiner_default not in RETIRED_GEMINI_MODELS, (
        f"paired_audit examiner_model default {examiner_default!r} is a retired gemini-2.5 model"
    )
    assert student_default not in RETIRED_GEMINI_MODELS, (
        f"paired_audit student_model default {student_default!r} is a retired gemini-2.5 model"
    )
