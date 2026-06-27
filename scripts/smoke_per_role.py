"""Acceptance smoke: per-role provider/model routing.

Proves that the generator, extract, and judge roles each route their RESOLVED
(provider, model) all the way to the agent spawn boundary, independently — the
core guarantee of the per-role-provider-model feature.

This drives the REAL code paths (`agent.summarize_lesson`, `phase_judge.judge`,
`pipeline._resolve_extract`, `model_tiers.resolve_judge`) and only stubs the two
leaf side effects: the subprocess spawn (`agent._spawn`) and the usage DB write
(`agent._record_usage`). That keeps it $0 and key-free while still exercising
the actual resolution + threading. (A real claude call isn't possible here —
no ANTHROPIC_API_KEY — and a full-homework API run is barred by the no-spam
money rule; routing, not model behavior, is what this feature changes.)

Reads the launch_defaults DB row for the extract fallback values.
Run:  uv run python -m scripts.smoke_per_role   (from the repo root)
Exit 0 + "SMOKE PASS" on success; raises AssertionError otherwise.
"""
import asyncio
from uuid import uuid4

from app.db import SessionLocal
from app.repositories import launch_defaults as launch_defaults_repo
from app.services import agent, model_tiers, phase_judge
from app.services.pipeline import _resolve_extract

_CAPTURED: list[dict] = []


async def _fake_spawn(*, provider, model, prompt, attachments, transport="cli"):
    # provider is a Provider object; record its name + the model + transport.
    _CAPTURED.append({"provider": provider.name, "model": model, "transport": transport})
    return (0, "ok", {"prompt_tokens": 1, "output_tokens": 1,
                      "cached_tokens": 0, "total_tokens": 2, "raw": {}}, "")


async def _fake_record(*args, **kwargs):
    return None


async def main() -> None:
    agent._spawn = _fake_spawn          # capture the spawn args, no subprocess
    agent._record_usage = _fake_record  # no DB write

    async with SessionLocal() as session:
        ld = await launch_defaults_repo.get(session)

    extract_kwargs = dict(
        book_text="Lesson 1: the cell is the basic unit of life.",
        section_title="The Cell", section_number="1", page_start=1, page_end=3,
        homework_job_id=uuid4(), phase_output_id=uuid4(), transport="cli",
    )

    # 1) EXTRACT honors an explicit per-job override (claude/opus), distinct from
    #    the generator and from the launch_defaults default.
    ep, em = _resolve_extract("claude", "claude-opus-4-7", ld)
    _CAPTURED.clear()
    await agent.summarize_lesson(provider=ep, model=em, **extract_kwargs)
    assert _CAPTURED[-1]["provider"] == "claude", _CAPTURED
    assert _CAPTURED[-1]["model"] == "claude-opus-4-7", _CAPTURED

    # 2) EXTRACT Auto (NULL override) falls back to the launch_defaults DB row
    #    (NOT settings attributes, which were deleted with the launch_defaults feature).
    ep2, em2 = _resolve_extract(None, None, ld)
    assert (ep2, em2) == (ld.extract_provider, ld.extract_model), (ep2, em2)
    _CAPTURED.clear()
    await agent.summarize_lesson(provider=ep2, model=em2, **extract_kwargs)
    assert _CAPTURED[-1]["provider"] == ld.extract_provider, (_CAPTURED, ep2)
    assert _CAPTURED[-1]["model"] == em2, (_CAPTURED, em2)

    # 3) JUDGE honors an explicit override, distinct from the generator.
    jp, jm = model_tiers.resolve_judge("gemini", "gemini-2.5-flash", "claude", "claude-opus-4-7")
    _CAPTURED.clear()
    await phase_judge.judge(
        subject="biology", phase_name="flashcards", output_md="# cards\n- a/b",
        lesson_context=None, prior_outputs={},
        gen_provider="gemini", gen_model="gemini-2.5-flash",
        judge_provider=jp, judge_model=jm, transport="cli",
    )
    assert any(c["provider"] == "claude" and c["model"] == "claude-opus-4-7"
               for c in _CAPTURED), _CAPTURED

    # 4) JUDGE self-grade is HARD-guarded: an explicit judge that resolves to the
    #    generator's model is swapped to a guaranteed-non-self judge (the auto-tier
    #    judge — NOT _SELF_FALLBACK, which would self-match a gemini-3.1-pro gen).
    #    Includes the model=None bypass case (judge provider=gemini, model=Auto).
    self_jp, self_jm = model_tiers.resolve_judge(
        "gemini", "gemini-3.1-pro-preview", "gemini", "gemini-3.1-pro-preview")
    assert (self_jp, self_jm) != ("gemini", "gemini-3.1-pro-preview"), (self_jp, self_jm)
    null_jp, null_jm = model_tiers.resolve_judge(
        "gemini", "gemini-3.1-pro-preview", "gemini", None)  # Auto model bypass
    assert (null_jp, null_jm) != ("gemini", "gemini-3.1-pro-preview"), (null_jp, null_jm)
    assert (null_jp, null_jm) != ("gemini", None), (null_jp, null_jm)

    # 5) JUDGE Auto (NULL override) uses the auto-tier judge.
    auto_jp, auto_jm = model_tiers.resolve_judge("gemini", "gemini-2.5-flash", None, None)
    assert (auto_jp, auto_jm) == model_tiers.judge_model_for("gemini", "gemini-2.5-flash")

    print("SMOKE PASS — extract/judge/generator route independently:")
    print(f"  extract override -> claude/claude-opus-4-7")
    print(f"  extract auto     -> {ep2}/{em2}")
    print(f"  judge override   -> {jp}/{jm}")
    print(f"  judge self-grade -> {self_jp}/{self_jm} (non-self swap)")
    print(f"  judge gemini+Auto self -> {null_jp}/{null_jm} (None bypass closed)")
    print(f"  judge auto       -> {auto_jp}/{auto_jm}")


if __name__ == "__main__":
    asyncio.run(main())
