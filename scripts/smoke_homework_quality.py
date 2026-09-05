"""Bounded real-review harness for paired homework-quality microfixtures.

The default command only lists fixtures. A real run requires ``--run`` plus an
explicit API provider/model, one to four complete pair IDs, a bounded
concurrency, and an output path. The script calls the production judge/solver
surfaces, never a provider SDK or generation/fleet path.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import dataclasses
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "homework_quality"
DEFAULT_FIXTURES = FIXTURE_ROOT / "fixtures.json"
LEARNER_POLICY = ROOT / "prompts" / "_general" / "_learner-quality.md"
MAX_PAIRS = 4
MAX_CONCURRENCY = 4


class FixtureError(ValueError):
    """A fixture catalogue is malformed or an unsafe scope was requested."""


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    pair_id: str
    variant: str
    defect_ids: tuple[str, ...]
    defect_class: str
    reviewer: str
    subject: str
    phase_name: str
    output_language: str
    control_tags: tuple[str, ...]
    source_refs: tuple[str, ...]
    contract: str
    lesson_context: str
    prior_outputs: Mapping[str, str]
    output_md: str
    expected_outcome: str
    decisive_evidence_groups: tuple[tuple[str, ...], ...]

    @property
    def contract_sha256(self) -> str:
        return _sha256(self.contract)

    @property
    def output_sha256(self) -> str:
        return _sha256(self.output_md)


@dataclass(frozen=True)
class Classification:
    status: str
    observed_outcome: str
    decisive_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunConfig:
    provider: str
    model: str
    transport: str
    concurrency: int = 1


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_fixture_file(relative: str, fixture_root: Path) -> str:
    target = (fixture_root / relative).resolve()
    root = fixture_root.resolve()
    if target.parent != root or not target.is_file():
        raise FixtureError(f"fixture output_file must be a file directly under {root}")
    return target.read_text(encoding="utf-8").rstrip("\r\n")


def load_fixtures(path: Path | str = DEFAULT_FIXTURES) -> list[Fixture]:
    """Load, resolve shared policy/contracts, and validate the fixture catalogue."""
    manifest_path = Path(path)
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"cannot load fixture catalogue: {exc}") from exc
    if raw.get("schema") != "homework-quality-microfixtures@1":
        raise FixtureError("unsupported fixture schema")
    contracts = raw.get("contracts")
    pairs = raw.get("pairs")
    if not isinstance(contracts, dict) or not isinstance(pairs, list):
        raise FixtureError("fixture catalogue needs contracts and pairs")
    policy = LEARNER_POLICY.read_text(encoding="utf-8").rstrip()
    fixture_root = manifest_path.resolve().parent
    fixtures: list[Fixture] = []
    try:
        for pair in pairs:
            reviewer = pair["reviewer"]
            focused_contract = contracts[reviewer].strip()
            output_language = pair["output_language"]
            if pair["subject"] == "english" and output_language == "uz":
                language_note = (
                    "This learner-facing microfixture uses Uzbek scaffolding with English "
                    "target-language terms."
                )
            else:
                language_name = {"en": "English", "uz": "Uzbek", "ru": "Russian"}[
                    output_language
                ]
                language_note = f"This learner-facing microfixture is in {language_name}."
            contract = (
                f"{policy}\n\n## Focused microfixture contract\n{focused_contract}\n"
                f"{language_note}"
            )
            for case in pair["cases"]:
                output_md = case.get("output_md")
                if output_md is None:
                    output_md = _safe_fixture_file(case["output_file"], fixture_root)
                fixtures.append(Fixture(
                    fixture_id=case["fixture_id"],
                    pair_id=pair["pair_id"],
                    variant=case["variant"],
                    defect_ids=tuple(pair.get("defect_ids", ())),
                    defect_class=pair["defect_class"],
                    reviewer=reviewer,
                    subject=pair["subject"],
                    phase_name=pair["phase_name"],
                    output_language=output_language,
                    control_tags=tuple(pair.get("control_tags", ())),
                    source_refs=tuple(pair.get("source_refs", ())),
                    contract=contract,
                    lesson_context=case.get("lesson_context", pair["lesson_context"]),
                    prior_outputs=dict(case.get("prior_outputs", pair.get("prior_outputs", {}))),
                    output_md=str(output_md).rstrip("\r\n"),
                    expected_outcome=case["expected_outcome"],
                    decisive_evidence_groups=tuple(
                        tuple(group) for group in case.get("decisive_evidence_all", ())
                    ),
                ))
    except (KeyError, TypeError) as exc:
        raise FixtureError(f"malformed fixture catalogue near {exc}") from exc
    validate_fixtures(fixtures)
    return fixtures


def group_pairs(fixtures: Sequence[Fixture]) -> dict[str, list[Fixture]]:
    grouped: dict[str, list[Fixture]] = defaultdict(list)
    for fixture in fixtures:
        grouped[fixture.pair_id].append(fixture)
    return dict(grouped)


def validate_fixtures(fixtures: Sequence[Fixture]) -> None:
    if not fixtures:
        raise FixtureError("fixture catalogue is empty")
    ids = [fixture.fixture_id for fixture in fixtures]
    if len(ids) != len(set(ids)):
        raise FixtureError("fixture IDs must be unique")
    allowed = {
        "judge": {"major", "finding", "no_major"},
        "solver": {"mismatch", "finding", "no_mismatch"},
    }
    for fixture in fixtures:
        if fixture.reviewer not in allowed:
            raise FixtureError(f"{fixture.fixture_id}: unsupported reviewer")
        if fixture.expected_outcome not in allowed[fixture.reviewer]:
            raise FixtureError(f"{fixture.fixture_id}: invalid expected outcome")
        if fixture.variant not in {"negative", "positive"}:
            raise FixtureError(f"{fixture.fixture_id}: invalid variant")
        if fixture.variant == "negative" and (
            len(fixture.decisive_evidence_groups) < 2
            or any(not group for group in fixture.decisive_evidence_groups)
        ):
            raise FixtureError(
                f"{fixture.fixture_id}: negative needs separate decisive evidence "
                "groups for the item and defect relationship"
            )
        if not all((fixture.contract, fixture.lesson_context, fixture.output_md)):
            raise FixtureError(f"{fixture.fixture_id}: blank reviewer input")
    for pair_id, pair in group_pairs(fixtures).items():
        if len(pair) != 2 or {item.variant for item in pair} != {"negative", "positive"}:
            raise FixtureError(f"{pair_id}: pair must contain negative and positive")
        for field in ("reviewer", "defect_ids", "subject", "phase_name", "output_language"):
            if len({getattr(item, field) for item in pair}) != 1:
                raise FixtureError(f"{pair_id}: pair differs in {field}")


def _outcome_text(outcome: Any) -> tuple[tuple[str, ...], str]:
    warnings = tuple(str(item) for item in (getattr(outcome, "warnings", None) or ()))
    feedback = str(getattr(outcome, "feedback", "") or "")
    return warnings, feedback


_WARNING_SEVERITY = re.compile(r"^\s*\[(major|minor|high|medium|low)\]", re.IGNORECASE)


def _warning_severity(warning: str) -> str | None:
    match = _WARNING_SEVERITY.match(warning)
    return match.group(1).casefold() if match else None


def classify_result(fixture: Fixture, outcome: Any) -> Classification:
    """Score a real reviewer outcome against verdict and planted-defect evidence."""
    if not getattr(outcome, "available", False) or getattr(outcome, "refused", False):
        return Classification("unverified", "unavailable")
    warnings, feedback = _outcome_text(outcome)
    del feedback  # Combined feedback cannot link one severity to one intended defect.
    groups = tuple(
        tuple(anchor.casefold() for anchor in group)
        for group in fixture.decisive_evidence_groups
    )
    decisive = tuple(text for text in warnings if groups and all(
        any(anchor in text.casefold() for anchor in group) for group in groups
    ))
    severities = {text: _warning_severity(text) for text in warnings}
    if fixture.reviewer == "judge":
        has_major = bool(getattr(outcome, "has_major", False))
        linked = tuple(text for text in decisive if severities[text] == "major")
        observed = "major" if has_major else "finding" if warnings else "clean"
        verdict_met = {
            "major": has_major and bool(linked),
            "finding": bool(decisive),
            "no_major": not has_major and "major" not in severities.values(),
        }[fixture.expected_outcome]
    else:
        has_mismatch = bool(getattr(outcome, "has_mismatch", False))
        linked = tuple(text for text in decisive if severities[text] == "high")
        observed = "mismatch" if has_mismatch else "finding" if warnings else "clean"
        verdict_met = {
            "mismatch": has_mismatch and bool(linked),
            "finding": bool(decisive),
            "no_mismatch": not has_mismatch and "high" not in severities.values(),
        }[fixture.expected_outcome]
    evidence_met = fixture.variant == "positive" or bool(decisive)
    credited = linked if fixture.expected_outcome in {"major", "mismatch"} else decisive
    return Classification("met" if verdict_met and evidence_met else "unmet", observed, credited)


def aggregate_pairs(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[str(result["pair_id"])].append(result)
    summary = []
    for pair_id, pair in sorted(grouped.items()):
        statuses = {str(item["status"]) for item in pair}
        status = "met" if statuses == {"met"} and len(pair) == 2 else (
            "unverified" if "unverified" in statuses else "unmet"
        )
        summary.append({"pair_id": pair_id, "status": status,
                        "fixtures": [item.get("fixture_id", item.get("variant")) for item in pair]})
    return summary


def result_exit_code(results: Sequence[Mapping[str, Any]]) -> int:
    pairs = aggregate_pairs(results)
    return 0 if pairs and all(pair["status"] == "met" for pair in pairs) else 1


_SENSITIVE_KEYS = {"authorization", "api_key", "apikey", "credential", "credentials",
                   "password", "secret", "access_token", "refresh_token"}
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_TOKEN_QUERY = re.compile(r"(?i)([?&](?:token|key|api_key|access_token)=)[^&#\s]+")


def sanitize_payload(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if str(key).casefold() in _SENSITIVE_KEYS
            else sanitize_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _TOKEN_QUERY.sub(r"\1[REDACTED]", _BEARER.sub("Bearer [REDACTED]", value))
    return value


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def write_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(sanitize_payload(payload)) + "\n", encoding="utf-8")


def reported_model(raw_envelope: Mapping[str, Any] | None) -> str | None:
    raw = raw_envelope or {}
    for key in ("modelVersion", "model", "served_model"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


JudgeCall = Callable[..., Awaitable[Any]]
SolverCall = Callable[..., Awaitable[Any]]


async def run_fixture(
    fixture: Fixture,
    config: RunConfig,
    *,
    judge_call: JudgeCall | None = None,
    solver_call: SolverCall | None = None,
    captured_reported_model: str | None = None,
) -> dict[str, Any]:
    from app.services import phase_judge, solver

    judge_call = judge_call or phase_judge.judge
    solver_call = solver_call or solver.solve
    common = dict(
        subject=fixture.subject,
        phase_name=fixture.phase_name,
        lesson_context=fixture.lesson_context,
        prior_outputs=dict(fixture.prior_outputs),
        output_language=fixture.output_language,
        transport=config.transport,
        homework_job_id=None,
        phase_output_id=None,
        contract_override=fixture.contract,
    )
    try:
        if fixture.reviewer == "judge":
            outcome = await judge_call(
                **common,
                output_md=fixture.output_md,
                gen_provider="fixture",
                gen_model=None,
                judge_provider=config.provider,
                judge_model=config.model,
            )
        else:
            outcome = await solver_call(
                **common,
                phase_output_md=fixture.output_md,
                solver_provider=config.provider,
                solver_model=config.model,
            )
        classification = classify_result(fixture, outcome)
        warnings, feedback = _outcome_text(outcome)
        refused = bool(getattr(outcome, "refused", False))
        error_type = None
    except Exception as exc:  # real auth/transport/control failures are unverified
        classification = Classification("unverified", "unavailable")
        warnings, feedback, refused = (), "", False
        error_type = type(exc).__name__
    return {
        "fixture_id": fixture.fixture_id,
        "pair_id": fixture.pair_id,
        "variant": fixture.variant,
        "defect_ids": list(fixture.defect_ids),
        "defect_class": fixture.defect_class,
        "reviewer": fixture.reviewer,
        "subject": fixture.subject,
        "phase_name": fixture.phase_name,
        "output_language": fixture.output_language,
        "control_tags": list(fixture.control_tags),
        "source_refs": list(fixture.source_refs),
        "expected_outcome": fixture.expected_outcome,
        "observed_outcome": classification.observed_outcome,
        "status": classification.status,
        "decisive_evidence": list(classification.decisive_evidence),
        "warnings": list(warnings),
        "feedback": feedback,
        "refused": refused,
        "error_type": error_type,
        "contract_sha256": fixture.contract_sha256,
        "output_sha256": fixture.output_sha256,
        "model": {
            "requested_provider": config.provider,
            "requested_model": config.model,
            "effective_provider": config.provider,
            "effective_model": config.model,
            "reported_model": captured_reported_model,
        },
    }


_ACTIVE_FIXTURE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "homework_quality_fixture", default=None
)


def _git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


async def execute_run(fixtures: Sequence[Fixture], config: RunConfig) -> dict[str, Any]:
    """Run selected probes with one temporary observer around production run_phase."""
    from app.services import agent

    semaphore = asyncio.Semaphore(config.concurrency)
    original_run_phase = agent.run_phase
    captured: dict[str, str | None] = {}

    async def observed_run_phase(**kwargs):
        result = await original_run_phase(**kwargs)
        fixture_id = _ACTIVE_FIXTURE.get()
        if fixture_id is not None:
            captured[fixture_id] = reported_model(getattr(result, "raw_envelope", None))
        return result

    async def one(fixture: Fixture) -> dict[str, Any]:
        async with semaphore:
            token = _ACTIVE_FIXTURE.set(fixture.fixture_id)
            try:
                result = await run_fixture(fixture, config)
                result["model"]["reported_model"] = captured.get(fixture.fixture_id)
                return result
            finally:
                _ACTIVE_FIXTURE.reset(token)

    agent.run_phase = observed_run_phase
    try:
        results = list(await asyncio.gather(*(one(fixture) for fixture in fixtures)))
    finally:
        agent.run_phase = original_run_phase
    policy = LEARNER_POLICY.read_text(encoding="utf-8").rstrip()
    return {
        "schema": "homework-quality-results@1",
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "code_revision": _git_revision(),
        "transport": config.transport,
        "learner_policy_sha256": _sha256(policy),
        "results": results,
        "pairs": aggregate_pairs(results),
        "verified": result_exit_code(results) == 0,
    }


def _select_pairs(fixtures: Sequence[Fixture], pair_ids: Sequence[str]) -> list[Fixture]:
    grouped = group_pairs(fixtures)
    unknown = [pair_id for pair_id in pair_ids if pair_id not in grouped]
    if unknown:
        raise FixtureError(f"unknown pair IDs: {', '.join(unknown)}")
    return [fixture for pair_id in pair_ids for fixture in grouped[pair_id]]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="make real production reviewer calls")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--transport", choices=("api", "cli"))
    parser.add_argument("--pair", action="append", default=[])
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        fixtures = load_fixtures(args.fixtures)
    except FixtureError as exc:
        parser.error(str(exc))
    grouped = group_pairs(fixtures)
    if not args.run:
        for pair_id, pair in sorted(grouped.items()):
            print(f"{pair_id}: {pair[0].reviewer} - {pair[0].defect_class}")
        return 0
    if not args.provider or not args.model or args.transport is None or args.output is None:
        parser.error("--run requires --provider, --model, --transport api, and --output")
    if args.transport != "api":
        parser.error("real homework-quality probes require --transport api")
    if not 1 <= len(args.pair) <= MAX_PAIRS or len(set(args.pair)) != len(args.pair):
        parser.error(f"--run requires 1 to {MAX_PAIRS} distinct --pair values")
    if not 1 <= args.concurrency <= MAX_CONCURRENCY:
        parser.error(f"--concurrency must be between 1 and {MAX_CONCURRENCY}")
    output_name = args.output.name.casefold()
    if args.output.suffix.casefold() != ".json" or re.search(
        r"credential|secret|password|api[-_]?key|token", output_name
    ):
        parser.error("--output must be a JSON results ledger, not a credential-like file")
    try:
        args.output.resolve().relative_to(FIXTURE_ROOT.resolve())
    except ValueError:
        pass
    else:
        parser.error("--output must stay outside the immutable fixture directory")
    try:
        selected = _select_pairs(fixtures, args.pair)
    except FixtureError as exc:
        parser.error(str(exc))
    from app.services.agent_models import is_valid, validate_transport
    if not is_valid(args.provider, args.model):
        parser.error("--provider/--model must name an explicit production manifest entry")
    transport_error = validate_transport(args.provider, args.model, args.transport)
    if transport_error:
        parser.error(transport_error)
    config = RunConfig(args.provider, args.model, args.transport, args.concurrency)
    report = asyncio.run(execute_run(selected, config))
    write_report(args.output, report)
    print(json_dumps({"output": str(args.output), "pairs": report["pairs"],
                      "verified": report["verified"]}))
    return result_exit_code(report["results"])


if __name__ == "__main__":
    raise SystemExit(main())
