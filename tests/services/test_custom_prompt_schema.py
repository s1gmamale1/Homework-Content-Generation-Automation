from app.api.v1.batch import BatchLaunchRequest
from app.schemas.job import GenerateRequest, JobOut


def test_generate_defaults_none():
    req = GenerateRequest()
    assert req.custom_prompts is None
    assert req.selected_phases is None


def test_generate_round_trips():
    req = GenerateRequest(custom_prompts={"flashcards": "RULES"}, selected_phases=["flashcards"])
    assert req.custom_prompts == {"flashcards": "RULES"}
    assert req.selected_phases == ["flashcards"]


def test_batch_round_trips():
    req = BatchLaunchRequest(
        book_id="00000000-0000-0000-0000-000000000001",
        custom_prompts={"reflection": "X"}, selected_phases=["reflection"],
    )
    assert req.custom_prompts == {"reflection": "X"}
    assert req.selected_phases == ["reflection"]


def test_jobout_added_phases_defaults_empty():
    # from_attributes build from a stub that lacks added_phases → default []
    class _J:
        id = "00000000-0000-0000-0000-000000000001"
        book_id = "00000000-0000-0000-0000-000000000002"
        toc_entry_id = "00000000-0000-0000-0000-000000000003"
        subject = "math-algebra"
        status = "pending"
        current_phase = None
        error_message = None
        provider = "claude"
        model = None
        transport = "cli"
        extract_transport = "inherit"
        judge_transport = "inherit"
        notion_skip_reason = None
    out = JobOut.model_validate(_J())
    assert out.added_phases == []
