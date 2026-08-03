from app.services import prompts


def test_structured_prompt_exists_for_pass1_phases():
    for phase in ("practice-rlc", "practice-sentence"):
        body = prompts.get_structured_prompt("history", phase, output_language="ru")
        assert body and "JSON" in body


def test_structured_prompt_absent_for_other_phases():
    assert prompts.get_structured_prompt("history", "flashcards", output_language="ru") is None


def test_structured_prompt_does_not_demand_markdown_only():
    body = prompts.get_structured_prompt("history", "practice-rlc", output_language="ru")
    assert "Markdown only" not in body
    assert "Respond in **Markdown only**" not in body


def test_structured_rlc_prompt_names_the_five_step_order():
    body = prompts.get_structured_prompt("history", "practice-rlc", output_language="ru")
    for kind in ("decision", "info_request", "final_decision", "concept_select", "reasoning"):
        assert kind in body
