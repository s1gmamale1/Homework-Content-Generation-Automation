# tests/schemas/test_solver_schema.py
import pytest
from pydantic import ValidationError
from app.schemas.solver import SolveVerdict, Discrepancy


def test_agrees_verdict_has_no_discrepancies():
    v = SolveVerdict(agrees=True, discrepancies=[])
    assert v.agrees is True and v.discrepancies == []


def test_discrepancy_roundtrip_and_confidence_literal():
    d = Discrepancy(item="card 9", generated_key="Oy option marked xato",
                    solver_answer="Oy symmetry is TRUE", explanation="both hold; origin composes",
                    confidence="high")
    v = SolveVerdict(agrees=False, discrepancies=[d])
    assert v.model_validate_json(v.model_dump_json()).discrepancies[0].confidence == "high"


def test_confidence_rejects_unknown_value():
    with pytest.raises(ValidationError):
        Discrepancy(item="x", generated_key="a", solver_answer="b",
                    explanation="c", confidence="certain")


def test_config_exposes_solver_knobs():
    from app.config import settings
    assert isinstance(settings.solver_enabled, bool)
    assert isinstance(settings.max_solve_regens, int) and settings.max_solve_regens >= 0
