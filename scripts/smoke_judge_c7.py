"""Acceptance smoke for Cluster 7: a real api judge call still returns a usable
verdict through the new refusal/de-comingle code. Run: uv run python -m scripts.smoke_judge_c7"""
import asyncio

from app.services import phase_judge


async def _main():
    out = await phase_judge.judge(
        subject="matematika",
        phase_name="case-based-preview",
        output_md="# Preview\n\nThis lesson introduces linear equations and how to isolate a variable.",
        lesson_context="The lesson covers solving linear equations by isolating the variable.",
        prior_outputs={},
        gen_provider="gemini", gen_model="gemini-3.5-flash",
        judge_provider="gemini", judge_model="gemini-3.5-flash",
        transport="api",
    )
    print(f"available={out.available} passed={out.passed} refused={out.refused} "
          f"warnings={out.warnings}")
    assert out.available is True, "judge should run + parse a Verdict through the new code"
    assert out.refused is False, "a normal output is not a refusal"
    print("SMOKE PASS: live judge returns a usable verdict through the c7 code path")


if __name__ == "__main__":
    asyncio.run(_main())
