"""The immutable launch contract a regeneration campaign is approved against.

A campaign is drafted, priced and approved at one moment and may launch hours
later, in waves, across a fleet. Everything that decides HOW a revision job runs
is therefore frozen here at draft time, serialized into
``regeneration_campaigns.launch_contract`` (JSONB), and read back verbatim when
each revision job is created — so every revision in a campaign is launched with
exactly the selection the operator approved.

This module is the ONLY definition of that contract; no later task defines a
second launch-contract type. It is also the ONLY place a draft is resolved:

    draft (``LaunchContract``)          — an operator's picks, "auto" allowed
      │  ``resolve_launch_contract(draft, defaults=…, session_limit_strategy=…)``
      │  called ONCE, in Task 7's ``create_campaign``, BEFORE the contract is
      │  persisted — one read of ``launch_defaults`` and one read of the
      │  fleet-wide ``settings.session_limit_strategy`` for the whole campaign
      ▼
    ``ResolvedLaunchContract``          — every selection concrete
      │  persisted to ``launch_contract``; the estimator prices THIS object
      ▼
    ``ensure_resolved(stored)``         — the persistence/read-back boundary

Everything downstream (the estimator, ``launch_canary``, ``approve_canary``,
Task 6's ``create_revision_job``) **copies** that stored object. None of them
takes a defaults argument and none of them may re-resolve: a campaign launches
in two waves separated by a human gate, so a second read of ``launch_defaults``
or of the restart-mutable ``SESSION_LIMIT_STRATEGY`` at a second moment gives
one immutable campaign two different meanings — the canary evidence would stop
describing the bulk, and the persisted approval record would no longer say what
the operator approved.

``resolve_launch_contract`` is pure: it reads no database row and no settings
object. Its caller does both reads, once, and hands the values in.

It validates through the SAME production helpers a normal launch uses
(``app.services.agent_models``) rather than restating their rules, so a manifest
change, a retired model or a new api-only model reaches regeneration for free.
``agent_models`` is a pure validation/lookup module — importing it here brings
no orchestration with it.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.services.agent_models import (
    default_model,
    is_valid,
    resolve_role_selection,
    resolve_role_transport,
    resolve_role_transport_default,
    validate_role_provider,
    validate_role_transport,
    validate_session_limit_strategy,
    validate_transport,
)

_ROLES = ("extract", "judge", "solver")

# The two values a revision may actually run with. `ck_homework_jobs_revision_
# session_limit_strategy` refuses everything else on a revision row, and
# `settings.session_limit_strategy` can never be 'inherit' (config.py rejects it
# at startup), so a resolved contract always carries one of these.
_CONCRETE_SESSION_LIMIT_STRATEGIES = ("pause", "switch")


class LaunchContract(BaseModel):
    """Frozen provider/model/transport selection for a campaign.

    Mirrors the ``HomeworkJob`` launch-option surface. Deliberately absent:
    ``kind`` (a revision is always ``homework``), ``custom_prompts`` and
    ``selected_phases`` — regeneration runs the CURRENT built-in prompts, and
    the phase set is the campaign/target phase plan, not a job-level subset;
    and ``output_language``.

    A revision's language is per **target** (``regeneration_targets.
    output_language``) and is copied verbatim from the immediate source job.
    A lineage is scoped by ``(toc_entry_id, output_language)`` and one campaign
    may legitimately hold a UZ *and* an RU target for the SAME lesson —
    ``uq_regeneration_targets_campaign_toc_language`` is
    ``UNIQUE(campaign_id, toc_entry_id, output_language)`` and discovery takes
    ``output_languages`` (plural) — so there is no single campaign-wide value
    to freeze. Freezing one would be an approved field with no read path, and
    an implementer who believed it would stamp it onto the revision instead of
    the source's, publishing an RU revision of a UZ lesson.

    This is the **draft** shape: ``model`` and the role providers may be
    ``None`` ("use the default") and ``session_limit_strategy`` may be
    ``'inherit'``. Those are draft INPUTS only — never stored contract values.
    ``resolve_launch_contract`` turns a draft into a ``ResolvedLaunchContract``
    once, in ``create_campaign``, before persistence.

    ``session_limit_strategy`` is resolved to a concrete ``'pause'``/``'switch'``
    at that single moment and then merely **copied** onto
    ``homework_jobs.session_limit_strategy`` by Task 6. A revision has
    ``batch_id=NULL`` (``ck_homework_jobs_revision_no_batch``), so there is no
    batch row to read the value back from at run time; the database refuses
    ``'inherit'`` on a revision as the backstop — by then the value should
    already have been concrete for the whole campaign.
    """

    # frozen: an approved contract must never be edited in place.
    # extra="forbid": a typo'd launch option must fail loudly at draft time,
    # not silently drop out of the JSON column and change how a revision runs.
    model_config = ConfigDict(frozen=True, extra="forbid")

    # ─── content phases ───────────────────────────────────────────────────
    provider: str
    model: Optional[str] = None
    transport: str = "cli"

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
    # Deliberately NOT here: `solver_enabled`. Solver enablement is existing
    # PROCESS-GLOBAL behavior (`settings.solver_enabled`, read by
    # `pipeline.py`'s `_solver_on`). There is no per-launch solver surface in
    # the product — not on `homework_jobs`, not on `batches`, not on
    # `launch_defaults` — and `jobs_repo.create` has no such parameter, so
    # freezing it here would promise a control that nothing can honour.
    # Regeneration OBSERVES and REPORTS the global setting at draft time
    # (informational) instead of freezing a nonexistent job option.

    @model_validator(mode="after")
    def _validate_against_production_rules(self) -> "LaunchContract":
        if not is_valid(self.provider, self.model):
            raise ValueError(
                f"invalid provider/model: {self.provider!r}/{self.model!r}"
            )
        for err in (
            validate_transport(self.provider, self.model, self.transport),
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
                # DRAFT INPUT ONLY — "use the launch default". It is never a
                # stored contract value: `resolve_launch_contract` replaces it
                # with the concrete pair before persistence, and
                # `ResolvedLaunchContract` refuses a `None` that survived.
                # (Ordinary launches stamp the resolved pair onto the job row
                # too — `jobs.py`/`batch.py` — so a revision left NULL here
                # would be the only unstamped job in the system, resolved from
                # a mutable row at whatever moment it happened to run.)
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


class LaunchDefaultsSnapshot(BaseModel):
    """The ``launch_defaults`` singleton row, read ONCE by ``create_campaign``.

    Passing the row's values in (rather than querying inside) is what makes
    ``resolve_launch_contract`` pure and testable, and what makes "resolved
    once" enforceable: there is exactly one place that loads this.

    Every column on ``launch_defaults`` is nullable so a partial PUT can touch a
    single field, so every field here is optional. A NULL role transport is the
    launcher's "Auto", i.e. ``'inherit'`` — not a missing value.

    ``from_attributes=True``: the caller validates the ORM row itself
    (``LaunchDefaultsSnapshot.model_validate(ld)``), not a hand-mapped dict —
    nine hand-copied fields would be nine chances to drop one, and the
    NULL→``'inherit'`` rule would have to be re-derived at the call site. The
    row's other columns (``toc_transport``, ``output_language``, the content
    pair) are simply not read: regeneration freezes the three ROLES here, and
    the content selection is the operator's explicit draft pick.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", from_attributes=True)

    extract_provider: Optional[str] = None
    extract_model: Optional[str] = None
    extract_transport: str = "inherit"
    judge_provider: Optional[str] = None
    judge_model: Optional[str] = None
    judge_transport: str = "inherit"
    solver_provider: Optional[str] = None
    solver_model: Optional[str] = None
    solver_transport: str = "inherit"

    # A FIELD validator, not a model-level one: it runs on BOTH input paths —
    # the ``launch_defaults`` ORM row (attributes) and a plain mapping — so the
    # "Auto means inherit" rule cannot depend on how the caller passed the row.
    @field_validator(
        "extract_transport", "judge_transport", "solver_transport", mode="before"
    )
    @classmethod
    def _null_transport_means_inherit(cls, value):
        return "inherit" if value is None else value

    @model_validator(mode="after")
    def _validate_transports(self) -> "LaunchDefaultsSnapshot":
        for role in _ROLES:
            err = validate_role_transport(
                f"{role}_transport", getattr(self, f"{role}_transport")
            )
            if err is not None:
                raise ValueError(err)
        return self


class ResolvedLaunchContract(LaunchContract):
    """A ``LaunchContract`` in which every draft "auto" has become a concrete
    value. This — not the draft — is what gets persisted, priced and copied.

    Concrete means: an explicit content ``model``; an explicit provider AND
    model for extract, judge and solver; and a ``session_limit_strategy`` of
    ``'pause'`` or ``'switch'``.

    A role transport of ``'inherit'`` IS concrete here and is deliberately
    allowed: it is a deterministic relation to ``transport``, which is already
    fixed on the contract, so ``resolve_role_transport(role, transport)`` gives
    the same answer at every later moment. Ordinary launches stamp ``'inherit'``
    onto job rows for exactly this reason (``jobs.py``'s
    ``resolve_role_transport_default`` may return the global default's own
    ``'inherit'``). The mutable inputs — the ``launch_defaults`` row and
    ``settings.session_limit_strategy`` — are the ones that must not be re-read,
    and after resolution neither of them is reachable from this object.

    Inherits ``frozen=True`` / ``extra="forbid"`` and re-runs the draft's full
    production-rule validation, which now covers every role (a role is skipped
    there only while its provider is ``None``, which this shape forbids).
    """

    @model_validator(mode="after")
    def _require_every_selection_concrete(self) -> "ResolvedLaunchContract":
        if self.model is None:
            raise ValueError(
                "unresolved contract: content model is None (provider-default)"
                " — name an explicit content model in the campaign draft. It "
                "is never substituted: `resolve_launch_contract` refuses a "
                "null content model rather than inventing one, because no "
                "ordinary launch path resolves it."
            )
        if self.session_limit_strategy not in _CONCRETE_SESSION_LIMIT_STRATEGIES:
            raise ValueError(
                "unresolved contract: session_limit_strategy "
                f"{self.session_limit_strategy!r} is not concrete (expected "
                "'pause' | 'switch') — resolve it once in create_campaign; "
                "re-resolving 'inherit' later re-reads the mutable fleet-wide "
                "default and gives one campaign two meanings"
            )
        for role in _ROLES:
            for field in (f"{role}_provider", f"{role}_model"):
                if getattr(self, field) is None:
                    raise ValueError(
                        f"unresolved contract: {field} is None — resolve it "
                        "against launch_defaults once in create_campaign; "
                        "leaving it NULL makes a revision the only job in the "
                        "system resolved from a mutable row at run time"
                    )
        return self


def resolve_launch_contract(
    draft: LaunchContract,
    *,
    defaults: LaunchDefaultsSnapshot,
    session_limit_strategy: str,
) -> ResolvedLaunchContract:
    """Turn an operator's draft into the campaign's one concrete meaning.

    Pure: no DB, no settings. Call it ONCE, in ``create_campaign``, before the
    contract is persisted.

    ``defaults`` is the ``launch_defaults`` row, loaded once by the caller.
    ``session_limit_strategy`` is the concrete strategy for this campaign,
    produced once by the caller as::

        agent_models.resolve_session_limit_strategy(draft.session_limit_strategy)

    — that helper is the production authority for "explicit pick wins, else the
    fleet-wide default", and calling it here is the single read of the mutable
    global. Passing anything but ``'pause'``/``'switch'`` is refused rather than
    silently re-resolved.

    Each ROLE is resolved through the production helpers
    (``resolve_role_selection``, ``resolve_role_transport_default``,
    ``default_model``) — the same ones ``app/api/v1/jobs.py`` and
    ``app/api/v1/batch.py`` use to stamp an ordinary launch — so regeneration
    cannot drift from Fleet behavior.

    The CONTENT model is deliberately NOT resolved: a draft that leaves it
    ``None`` is REFUSED. Parity demands that — ``jobs.py``/``batch.py`` pass
    ``model=body.model`` verbatim and never call ``default_model`` for content
    — and the operator's configured content default
    (``launch_defaults.content_provider``/``content_model``) is not reachable
    from here, since ``LaunchDefaultsSnapshot`` carries the three roles only.

    Validation of the resolved result happens in ``LaunchContract``'s own
    validator when the ``ResolvedLaunchContract`` is constructed.
    """
    err = validate_session_limit_strategy(session_limit_strategy)
    if err is not None:
        raise ValueError(err)
    if session_limit_strategy not in _CONCRETE_SESSION_LIMIT_STRATEGIES:
        raise ValueError(
            f"session_limit_strategy {session_limit_strategy!r} is not concrete: "
            "pass agent_models.resolve_session_limit_strategy(draft."
            "session_limit_strategy), which reads the fleet-wide default once"
        )

    if draft.model is None:
        raise ValueError(
            "no content model to resolve — name an explicit content model in "
            "the campaign draft. Resolution cannot invent one: an ordinary "
            "launch does not resolve it either (`jobs.py`/`batch.py` pass the "
            "operator's `model` verbatim), and the operator's configured "
            "content default lives in `launch_defaults.content_model`, which "
            "this pure function deliberately cannot read. Substituting "
            f"default_model({draft.provider!r}) would freeze "
            f"{default_model(draft.provider)!r} — the head of MODEL_MANIFEST, "
            "not the operator's default and not a value any launch path "
            "produces — onto every revision in the campaign."
        )

    fields: dict[str, object] = {
        "model": draft.model,
        "session_limit_strategy": session_limit_strategy,
    }
    for role in _ROLES:
        provider, model = resolve_role_selection(
            getattr(draft, f"{role}_provider"),
            getattr(draft, f"{role}_model"),
            getattr(defaults, f"{role}_provider"),
            getattr(defaults, f"{role}_model"),
        )
        if provider is None:
            raise ValueError(
                f"{role}: no provider to resolve — neither the draft contract "
                f"nor launch_defaults.{role}_provider carries one"
            )
        fields[f"{role}_provider"] = provider
        # A default row may name a provider but no model (the columns are
        # independently nullable); complete it with THAT provider's default,
        # the same rule resolve_role_selection applies to an explicit pick.
        # This is a deliberate COMPLETION, not parity with `jobs.py`: an
        # ordinary launch would stamp NULL here and then fail loudly at
        # `validate_transport` on an api launch rather than substitute
        # `MODEL_MANIFEST[provider][0]`. It is defensive only — `settings.py`
        # guarantees `launch_defaults.<role>_model` is never NULL — and a
        # stored contract may not carry a NULL role model at all
        # (`ResolvedLaunchContract`), so the alternative here would be to
        # refuse. Unlike the content model, the role pair has a real
        # operator-configured default this function DOES read, so completing
        # it reproduces what the operator set rather than inventing one.
        fields[f"{role}_model"] = model or default_model(provider)
        fields[f"{role}_transport"] = resolve_role_transport_default(
            getattr(draft, f"{role}_transport"),
            getattr(defaults, f"{role}_transport"),
        )
    return ResolvedLaunchContract(**{**draft.model_dump(), **fields})


def ensure_resolved(contract) -> ResolvedLaunchContract:
    """The persistence / read-back boundary: refuse an unresolved contract.

    Accepts an already-resolved contract (returned unchanged), a draft
    ``LaunchContract``, or the raw dict read back from the ``launch_contract``
    JSONB column. Raises ``pydantic.ValidationError`` if any selection is still
    an "auto" placeholder.

    It deliberately takes no ``defaults`` and no strategy argument: a reader
    CANNOT resolve, only verify. That is what stops a second resolution from
    creeping into ``launch_canary``, ``approve_canary`` or
    ``create_revision_job``.
    """
    if isinstance(contract, ResolvedLaunchContract):
        return contract
    if isinstance(contract, LaunchContract):
        return ResolvedLaunchContract(**contract.model_dump())
    return ResolvedLaunchContract.model_validate(contract)
