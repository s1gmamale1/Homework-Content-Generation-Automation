"""Retired-model guard shared by every path that reactivates a job while
preserving its pinned provider/model: single-job retry (``jobs.py::retry_job``),
batch resume (``jobs_repo.resume_failed_in_batch``), and relaunch-resume
(``batch.py``'s launch loop adopting a saved failed/cancelled section).

gemini-2.5 (pro/flash/flash-lite) was retired 2026-08-03 — the family 404s on
the production API key (see ``agent_models.RETIRED_GEMINI_MODELS``). A job
that was stamped with one of these models before the cutover must never be
silently re-fired; the three reactivation paths above all reuse the job's
pinned provider/model instead of re-resolving it, so without this guard a
retry/resume would call a dead model.
"""

from __future__ import annotations

from app.services.agent_models import RETIRED_GEMINI_MODELS

# (role name, provider attribute, model attribute) for each of the four
# independent role pairs a HomeworkJob carries. "content" is the bare
# provider/model columns (there is no `content_provider` column — see
# app/models/homework_job.py).
_ROLE_ATTRS: tuple[tuple[str, str, str], ...] = (
    ("content", "provider", "model"),
    ("extract", "extract_provider", "extract_model"),
    ("judge", "judge_provider", "judge_model"),
    ("solver", "solver_provider", "solver_model"),
)


def retired_models_in_job(job) -> list[tuple[str, str, str]]:
    """Return ``(role, provider, model)`` for every role pair on ``job`` that
    is pinned to a retired gemini model.

    A pair is "retired" iff ``provider == "gemini"`` AND ``model`` is in
    ``RETIRED_GEMINI_MODELS`` — the retirement is a gemini-specific fact, so a
    different provider whose model string happens to collide with a retired
    gemini model name is never flagged. NULL provider or model (a role left at
    "inherit"/unset) is skipped, not flagged.
    """
    hits: list[tuple[str, str, str]] = []
    for role, provider_attr, model_attr in _ROLE_ATTRS:
        provider = getattr(job, provider_attr, None)
        model = getattr(job, model_attr, None)
        if provider is None or model is None:
            continue
        if provider == "gemini" and model in RETIRED_GEMINI_MODELS:
            hits.append((role, provider, model))
    return hits
