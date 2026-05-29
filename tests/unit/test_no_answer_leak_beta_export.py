"""No server-only answer field may reach the student-facing beta payload.

Flow v2 plan §10: is_correct, consequence, acceptable_keywords (and answer_spec)
must be absent from the student payload. We assert recursively across the whole
nested structure, not just the top level.
"""

import json

from app.services import beta_export
from tests.unit.test_beta_platform_contract import make_canonical

LEAK_FIELDS = ("is_correct", "consequence", "acceptable_keywords", "answer_spec")


class TestNoAnswerLeak:
    def test_top_and_nested_keys_stripped(self):
        payload, _ = beta_export.adapt_to_beta(make_canonical())
        blob = json.dumps(payload)
        for field in LEAK_FIELDS:
            assert f'"{field}"' not in blob, f"{field} leaked into student payload"

    def test_options_retain_label_but_not_answer(self):
        payload, _ = beta_export.adapt_to_beta(make_canonical())
        decision = payload["steps"][0]
        assert decision["kind"] == "decision"
        assert decision["options"], "options should still be present"
        for opt in decision["options"]:
            assert "label" in opt
            assert "is_correct" not in opt
            assert "consequence" not in opt

    def test_reasoning_keeps_prompt_drops_keywords(self):
        payload, _ = beta_export.adapt_to_beta(make_canonical())
        reasoning = payload["steps"][-1]
        assert reasoning["kind"] == "reasoning"
        assert "prompt" in reasoning
        assert "min_chars" in reasoning
        assert "acceptable_keywords" not in reasoning

    def test_concept_chips_lose_correct_flag(self):
        payload, _ = beta_export.adapt_to_beta(make_canonical())
        concept = payload["steps"][3]
        assert concept["kind"] == "concept_select"
        for chip in concept["concept_chips"]:
            assert "label" in chip
            assert "is_correct" not in chip
