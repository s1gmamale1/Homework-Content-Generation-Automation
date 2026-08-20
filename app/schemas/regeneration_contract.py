"""The immutable launch contract a regeneration campaign is approved against.

A campaign is drafted, priced and approved at one moment and may launch hours
later, in waves, across a fleet. Everything that decides HOW a revision job runs
is therefore frozen here at draft time, serialized into
``regeneration_campaigns.launch_contract`` (JSONB), and read back verbatim when
each revision job is created — so every revision in a campaign is launched with
exactly the selection the operator approved.

This module is the ONLY definition of that contract; no later task defines a
second launch-contract type.

It validates through the SAME production helpers a normal launch uses
(``app.services.agent_models``) rather than restating their rules, so a manifest
change, a retired model or a new api-only model reaches regeneration for free.
``agent_models`` is a pure validation/lookup module — importing it here brings
no orchestration with it.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, model_validator

from app.services.agent_models import (
    is_valid,
    resolve_role_transport,
    validate_output_language,
    validate_role_provider,
    validate_role_transport,
    validate_session_limit_strategy,
    validate_transport,
)

_ROLES = ("extract", "judge", "solver")


class LaunchContract(BaseModel):
    """Frozen provider/model/transport/language selection for a campaign.

    Mirrors the ``HomeworkJob`` launch-option surface. Deliberately absent:
    ``kind`` (a revision is always ``homework``), ``custom_prompts`` and
    ``selected_phases`` — regeneration runs the CURRENT built-in prompts, and
    the phase set is the campaign/target phase plan, not a job-level subset.
    ``session_limit_strategy`` is carried here (and only here) because revision
    jobs have ``batch_id=NULL`` and so have no batch row to read it from.
    """

    # frozen: an approved contract must never be edited in place.
    # extra="forbid": a typo'd launch option must fail loudly at draft time,
    # not silently drop out of the JSON column and change how a revision runs.
    model_config = ConfigDict(frozen=True, extra="forbid")

    # ─── content phases ───────────────────────────────────────────────────
    provider: str
    model: Optional[str] = None
    transport: str = "cli"
    output_language: str

    # ─── per-role overrides (None/"inherit" = follow the job-level pick) ───
    extract_transport: str = "inherit"
    extract_provider: Optional[str] = None
    extract_model: Optional[str] = None
    judge_transport: str = "inherit"
    judge_provider: Optional[str] = None
    judge_model: Optional[str] = None
    solver_transport: str = "inherit"
    solver_provider: Optional[str] = None
    solver_model: Optional[str] = None

    # ─── run policy ───────────────────────────────────────────────────────
    session_limit_strategy: str = "inherit"
    solver_enabled: bool = True

    @model_validator(mode="after")
    def _validate_against_production_rules(self) -> "LaunchContract":
        if not is_valid(self.provider, self.model):
            raise ValueError(
                f"invalid provider/model: {self.provider!r}/{self.model!r}"
            )
        for err in (
            validate_transport(self.provider, self.model, self.transport),
            validate_output_language(self.output_language, allow_none=False),
            validate_session_limit_strategy(self.session_limit_strategy),
        ):
            if err is not None:
                raise ValueError(err)

        for role in _ROLES:
            role_transport = getattr(self, f"{role}_transport")
            err = validate_role_transport(f"{role}_transport", role_transport)
            if err is not None:
                raise ValueError(err)

            provider = getattr(self, f"{role}_provider")
            if provider is None:
                # No explicit pick: the role resolves against launch_defaults at
                # launch time and is validated there, exactly as a normal launch.
                continue
            model = getattr(self, f"{role}_model")
            if not is_valid(provider, model):
                raise ValueError(
                    f"{role}: unknown (provider, model) ({provider!r}, {model!r})"
                )
            if err := validate_role_provider(role, provider):
                raise ValueError(err)
            effective = resolve_role_transport(role_transport, self.transport)
            if err := validate_transport(provider, model, effective):
                raise ValueError(f"{role}: {err}")
        return self
