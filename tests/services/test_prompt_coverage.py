"""Every phase a subject can run must have a prompt file on disk.

Plan §10 fail-fast rule: "if an enabled phase has no prompt in
``prompts/<subject>/``, generation fails rather than inventing content." This
test makes that a build-time guarantee instead of a runtime surprise — it walks
every phase in every subject's easy/hard sequence (plus ``classify`` where the
subject classifies) and asserts the prompt loads.
"""

from __future__ import annotations

import pytest

from app.services.flows import SUBJECT_FLOWS
from app.services.prompts import get_prompt

# ``extract`` is a builtin prompt (no file); every other phase needs one.
_BUILTIN = {"extract"}


def _required_prompts() -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for subject, flow in SUBJECT_FLOWS.items():
        phases: set[str] = set()
        if flow.get("has_classify"):
            phases.add("classify")
        for seq_name in ("easy", "hard"):
            phases |= set(flow.get(seq_name, []))
        for phase in phases - _BUILTIN:
            pairs.add((subject, phase))
    return pairs


@pytest.mark.parametrize("subject,phase", sorted(_required_prompts()))
def test_every_flow_phase_has_a_prompt(subject: str, phase: str) -> None:
    body = get_prompt(subject, phase)
    assert body.strip(), f"prompt {subject}/{phase}.md is empty"
