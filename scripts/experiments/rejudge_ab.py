"""Acceptance B — three-arm re-judge experiment + behavioral safety probes.

Committed, reproducible re-judge of a stored 40-phase case-control sample across
three arms that differ ONLY in the judge's source-fidelity rule and/or the phase
authoring contract:

  * A  — OLD `_FIDELITY_RULE` + OLD contracts   (both pinned to the immutable
         branch-point SHA 57b81aa; contracts passed via `contract_override`)      -> production baseline
  * B  — NEW rule            + OLD contracts     (contract_override = old render)  -> isolates Task 1 (the rule)
  * C  — NEW rule            + NEW contracts      (no override -> production get_prompt) -> shipped state

plus four constructed behavioral safety probes run through the NEW rule.

These are REAL, billed `transport=api` judge calls (gemini). They write normal
`agent_usages` billing rows but NEVER touch `phase_outputs` / `judge_status` — no
persisted operational re-judge. Budget is hard-bounded by --max-calls / --max-cost-usd;
on hitting either the run stops cleanly and writes a partial artifact with a
`budget_hit` marker enumerating the skipped work.

Sanitized: no tokens/keys in this file; the DB URL comes only from the environment
(`.env` via app.config). Run from the repo root:

    uv run python scripts/experiments/rejudge_ab.py [--seed S] [--max-calls 200] [--max-cost-usd 6.0]

Design notes:
  * Counterbalancing (merge-gate finding 2, corrected): arm execution order is rotated
    PER ITEM by `item_index % 3` (0->ABC, 1->BCA, 2->CAB) rather than grouped by arm —
    the earlier grouped-by-arm design (all A, then B+C) was rejected because it let a
    systematic call-order confound ride alongside the rule/contract manipulation even
    though gemini verdicts are unseeded per call.
  * Concurrency tradeoff: the OLD-rule arm (A) monkeypatches the process-global
    `phase_judge._FIDELITY_RULE`, which is read at judge-prompt build time. Combined
    with per-item rotation, this means an A call can be immediately adjacent (in
    wall-clock terms) to a B/C call for a DIFFERENT item if calls ran concurrently —
    the global has no per-call scoping, so ANY overlap between an A call and a
    non-A call anywhere in the run is a correctness bug (wrong rule text sent to the
    judge), not just within one item. The fix is to run every arm call — the main
    3-arm pass AND the discordant replays — FULLY SEQUENTIALLY, one judge call at a
    time, with the monkeypatch applied in a try/finally around EACH individual arm-A
    call (`run_arm_call`). This trades wall-clock time for a hard concurrency
    guarantee: ~120 main-pass calls + up to 48 replay calls + 12 probe calls at
    ~30s/call is roughly 1.5-2h end-to-end instead of the few minutes a fully
    concurrent run would take. Only the probes (which never touch the OLD rule) keep
    their semaphore-bounded concurrency.
  * Only `prompts/_general/flashcards.md` and the appended uz label clause in
    `prompts._resolve_language_rule` changed between 57b81aa and HEAD; the CBP md and
    the FAMILY_RULES blocks are byte-identical. The OLD contract render reuses the
    (frozen) FAMILY_RULES + base language blocks from the live module and only strips
    the new uz clause / swaps in the OLD md body — so arms differ ONLY in the intended
    text.

Modes:
  * default          — full three-arm run + probes (real, billed judge calls).
  * --probes-only     — re-run only the 12 safety-probe calls (real, billed calls).
  * --recompute-only  — ZERO model calls. Re-derives consolidated verdicts, transition
    tables, reweighted population rates, and the residual breakdown from the RAW
    VERDICTS already recorded in the on-disk artifact, using the corrected Defect-1
    consolidation (majority of the 3 REPLAY runs only; the original run stays recorded
    but does not vote). Appends a `corrections` entry documenting what changed.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import contextvars
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from app.config import settings  # noqa: E402  (loads .env -> DATABASE_URL, SA creds)
from app.services import agent, model_tiers, phase_judge, pricing, prompts, subjects  # noqa: E402

import asyncpg  # noqa: E402

# ── pinned experiment constants ──────────────────────────────────────────────
BRANCH_SHA = "57b81aa"                      # immutable branch-point (arm A source of truth)
GEN_PROVIDER = "gemini"                     # stored generator (all 407 cohort jobs identical)
GEN_MODEL = "gemini-3-flash-preview"        # ACTUAL stored generator model (brief said 2.5-flash)
JUDGE_PROVIDER_STAMP = "gemini"             # stored judge_provider
JUDGE_MODEL_STAMP = "gemini-2.5-flash"      # stored judge_model
TRANSPORT = "api"
MATH_BATCH_PREFIXES = ["4a380da8", "bd51015b", "95f49c30", "0fb09b6c"]
PHASES = ["case-based-preview", "flashcards"]
COHORTS = ["math", "geo"]
STATUSES = ["major", "clean"]              # prior judge_status class
PER_CELL = 5
DISCORDANT_CAP = 8                          # first N discordant items replayed
REPLAY_RUNS = 3                             # fresh runs per deciding arm for a replayed item
DEFAULT_SEED = "rejudge-ab-2026-07-23"
# Per-item counterbalanced arm-execution order (Defect 2 fix), keyed by item_index % 3.
ARM_ROTATION = {0: "ABC", 1: "BCA", 2: "CAB"}

ARTIFACT_PATH = Path("scripts/experiments/2026-07-23-rejudge-ab-results.json")


def dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def sha(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


# ── OLD text extraction from the immutable SHA ───────────────────────────────
def git_show(path: str) -> str:
    return subprocess.check_output(["git", "show", f"{BRANCH_SHA}:{path}"], text=True)


def old_fidelity_rule() -> str:
    """The OLD `_FIDELITY_RULE` string literal, parsed from the 57b81aa source via
    ast.literal_eval (implicit string concatenation folds to one Constant)."""
    src = git_show("app/services/phase_judge.py")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_FIDELITY_RULE":
                    return ast.literal_eval(node.value)
    raise RuntimeError("could not locate OLD _FIDELITY_RULE in 57b81aa")


_OLD_MD_CACHE: dict[str, str] = {}


def old_md_body(phase: str) -> str:
    if phase not in _OLD_MD_CACHE:
        _OLD_MD_CACHE[phase] = git_show(f"prompts/_general/{phase}.md")
    return _OLD_MD_CACHE[phase]


def old_language_rule(subject: str, output_language: str) -> str:
    """Reproduce prompts._resolve_language_rule AS OF 57b81aa: identical to the live
    function except it does NOT append `_LOCALIZE_HEADINGS_CLAUSE_UZ` for uz/default
    output. Reuses the live (frozen) base blocks so nothing else drifts."""
    sd = subjects.REGISTRY.get(subject)
    if sd and sd.language in ("english", "russian"):
        rule = prompts._l2_rule(sd.language, output_language)
    else:
        rule = prompts.MEDIUM_RULES.get(output_language, prompts.MEDIUM_RULES["uz"])
    if (output_language or "").lower() in ("en", "ru"):
        rule = rule + prompts._LOCALIZE_HEADINGS_CLAUSE
    # OLD: no uz-clause append (the only behavioral change vs HEAD)
    return rule


def render_old_contract(subject: str, phase: str, output_language: str) -> str:
    """Render the OLD (57b81aa) contract exactly as production get_prompt would have,
    mimicking get_prompt's substitutions minimally against the OLD md body + OLD
    language rule. FAMILY_RULES blocks are byte-identical old/new, so the live dict
    is used directly."""
    body = old_md_body(phase)
    body = body.replace("{{SUBJECT}}", prompts.SUBJECT_LABELS.get(subject, subject))
    body = body.replace("{{LANGUAGE_RULES}}", old_language_rule(subject, output_language))
    blocks = prompts.FAMILY_RULES.get(phase, {})
    fam = prompts._SUBJECT_FAMILY.get(subject)
    family_block = blocks.get(fam) or blocks.get("_default", "")
    body = body.replace("{{FAMILY_RULES}}", family_block)
    return body


def new_contract(subject: str, phase: str, output_language: str) -> str:
    """The shipped production contract (arm C)."""
    return prompts.get_prompt(subject, phase, output_language=output_language)


# ── per-call token attribution (contextvar bucket; concurrency-safe) ─────────
_bucket: contextvars.ContextVar[list | None] = contextvars.ContextVar("usage_bucket", default=None)
_orig_record = agent._record_usage


async def _wrapped_record(**kw):
    b = _bucket.get()
    if b is not None:
        b.append({
            "provider": kw.get("provider"),
            "model": kw.get("model_name"),
            "usage": dict(kw.get("usage") or {}),
            "success": kw.get("success"),
            "operation": kw.get("operation"),
        })
    return await _orig_record(**kw)


agent._record_usage = _wrapped_record


# ── cumulative budget across invocations (Defect 3 fix) ──────────────────────
def load_prior_budget() -> tuple[int, float]:
    """Load the calls/cost already recorded on disk from a PRIOR invocation, so a
    fresh invocation's cap check is CUMULATIVE across invocations rather than
    silently resetting to zero. This is the fix for the bug where `--probes-only`
    (or any invocation layered onto an existing artifact) started a fresh Budget(0, 0)
    each time, so the artifact could show calls_made=219 against max_calls=200 with
    budget_hit=false — each individual invocation stayed under cap in isolation while
    the aggregate blew past it silently."""
    if not ARTIFACT_PATH.exists():
        return 0, 0.0
    try:
        prior = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8")).get("budget", {})
    except (json.JSONDecodeError, OSError):
        return 0, 0.0
    return int(prior.get("calls_made", 0) or 0), float(prior.get("actual_cost_usd", 0.0) or 0.0)


def refuse_if_cumulative_cap_hit(prior_calls: int, prior_cost: float,
                                  max_calls: int, max_cost: float) -> None:
    """Hard-refuse to start (no DB connect, no model calls) if the CUMULATIVE spend
    already recorded in the artifact is at/over the requested cap. Raising the cap
    (or archiving/removing the artifact) is the explicit, visible way past this —
    never a silent fresh-budget reset."""
    if prior_calls >= max_calls or prior_cost >= max_cost:
        print(
            "REFUSING TO START — cumulative budget already at/over cap.\n"
            f"  prior calls_made   = {prior_calls} (cap --max-calls {max_calls})\n"
            f"  prior actual_cost  = ${prior_cost:.4f} (cap --max-cost-usd ${max_cost:.2f})\n"
            f"  artifact           = {ARTIFACT_PATH}\n"
            "Raise --max-calls/--max-cost-usd (renewed approval) or archive/remove the "
            "artifact to start a fresh budget. No calls were made.",
            file=sys.stderr,
        )
        sys.exit(3)


# ── budget-guarded judge runner ──────────────────────────────────────────────
class Budget:
    """`calls`/`cost` are CUMULATIVE across invocations (seeded from `prior_calls`/
    `prior_cost` on construction) — they are what gates `reserve()` against the cap.
    `new_calls`/`new_cost` count only what THIS invocation made, for reporting and for
    incrementing the artifact's per-invocation fields (e.g. `probe_rerun_calls`)."""

    def __init__(self, max_calls: int, max_cost: float,
                 prior_calls: int = 0, prior_cost: float = 0.0):
        self.max_calls = max_calls
        self.max_cost = max_cost
        self.calls = prior_calls
        self.cost = prior_cost
        self.new_calls = 0
        self.new_cost = 0.0
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.hit = prior_calls >= max_calls or prior_cost >= max_cost
        self.skipped: list[dict] = []
        self._lock = asyncio.Lock()

    async def reserve(self, meta: dict) -> bool:
        async with self._lock:
            if self.hit or self.calls >= self.max_calls or self.cost >= self.max_cost:
                self.hit = True
                self.skipped.append(meta)
                return False
            self.calls += 1
            self.new_calls += 1
            return True

    async def settle(self, bucket: list):
        pt = sum(int(u["usage"].get("prompt_tokens") or 0) for u in bucket)
        ot = sum(int(u["usage"].get("output_tokens") or 0) for u in bucket)
        ct = sum(int(u["usage"].get("cached_tokens") or 0) for u in bucket)
        cost = sum(pricing.cost_usd(u["provider"], u["model"], u["usage"]) for u in bucket)
        async with self._lock:
            self.cost += cost
            self.new_cost += cost
            self.prompt_tokens += pt
            self.output_tokens += ot
            self.cached_tokens += ct
            if self.cost >= self.max_cost:
                self.hit = True
        return cost, pt, ot, ct


async def run_one_judge(budget: Budget, meta: dict, **judge_kwargs) -> dict | None:
    """One budget-guarded judge call. Returns a verdict dict, or None if skipped."""
    if not await budget.reserve(meta):
        return None
    bucket: list = []
    token = _bucket.set(bucket)
    try:
        outcome = await phase_judge.judge(**judge_kwargs)
        cost, pt, ot, ct = await budget.settle(bucket)
        return {
            "available": outcome.available,
            "passed": outcome.passed,
            "has_major": outcome.has_major,
            "refused": outcome.refused,
            "warnings": outcome.warnings,
            "cost_usd": round(cost, 6),
            "prompt_tokens": pt,
            "output_tokens": ot,
            "cached_tokens": ct,
        }
    except Exception as exc:  # noqa: BLE001 — capture, never abort the whole run
        await budget.settle(bucket)
        return {
            "available": False, "passed": None, "has_major": None, "refused": False,
            "warnings": [f"EXCEPTION: {type(exc).__name__}: {exc}"],
            "cost_usd": 0.0, "prompt_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
            "error": True,
        }
    finally:
        _bucket.reset(token)


# ── sampling ─────────────────────────────────────────────────────────────────
async def sample_cells(conn, seed: str) -> list[dict]:
    items: list[dict] = []
    math_filter = " OR ".join(
        f"b.id::text LIKE '{p}%'" for p in MATH_BATCH_PREFIXES
    )
    for cohort in COHORTS:
        if cohort == "math":
            job_join = f"JOIN batches b ON b.id = j.batch_id AND ({math_filter})"
            job_where = "j.status = 'done'"
        else:
            job_join = ""
            job_where = "j.subject = 'geografiya' AND j.status = 'done'"
        for phase in PHASES:
            for status in STATUSES:
                if status == "major":
                    status_cond = "p.judge_status = 'major_shipped'"
                else:
                    status_cond = "(p.judge_status IS NULL OR p.judge_status NOT LIKE 'major%')"
                q = f"""
                    SELECT p.id::text AS phase_output_id, p.job_id::text AS job_id,
                           p.phase_name, p.output_md, p.validation_warnings, p.judge_status,
                           j.subject, j.output_language,
                           (SELECT e.output_md FROM phase_outputs e
                              WHERE e.job_id = j.id AND e.phase_name = 'extract'
                                AND COALESCE(e.output_md,'') <> '' LIMIT 1) AS extract_md
                    FROM phase_outputs p
                    JOIN homework_jobs j ON j.id = p.job_id
                    {job_join}
                    WHERE {job_where}
                      AND p.phase_name = $1
                      AND p.status = 'done'
                      AND COALESCE(p.output_md,'') <> ''
                      AND {status_cond}
                      AND EXISTS (SELECT 1 FROM phase_outputs e
                                    WHERE e.job_id = j.id AND e.phase_name = 'extract'
                                      AND COALESCE(e.output_md,'') <> '')
                    ORDER BY md5($2 || p.id::text)
                    LIMIT {PER_CELL}
                """
                rows = await conn.fetch(q, phase, seed)
                for r in rows:
                    items.append({
                        "cohort": cohort,
                        "phase": phase,
                        "prior_status": status,
                        "phase_output_id": r["phase_output_id"],
                        "job_id": r["job_id"],
                        "subject": r["subject"],
                        "output_language": r["output_language"] or "uz",
                        "output_md": r["output_md"],
                        "extract_md": r["extract_md"],
                        "validation_warnings": r["validation_warnings"],
                    })
    # stable ordering => deterministic item_index for counterbalancing / discordant cap
    items.sort(key=lambda it: (it["cohort"], it["phase"], it["prior_status"],
                               hashlib.md5((seed + it["phase_output_id"]).encode()).hexdigest()))
    for i, it in enumerate(items):
        it["item_index"] = i
    return items


async def prevalence(conn) -> dict:
    out: dict = {}
    math_filter = " OR ".join(f"b.id::text LIKE '{p}%'" for p in MATH_BATCH_PREFIXES)
    for cohort in COHORTS:
        if cohort == "math":
            job_join = f"JOIN batches b ON b.id = j.batch_id AND ({math_filter})"
            job_where = "j.status = 'done'"
        else:
            job_join = ""
            job_where = "j.subject = 'geografiya' AND j.status = 'done'"
        for phase in PHASES:
            q = f"""
                SELECT
                  COUNT(*) FILTER (WHERE p.judge_status = 'major_shipped') AS major,
                  COUNT(*) AS total
                FROM phase_outputs p
                JOIN homework_jobs j ON j.id = p.job_id
                {job_join}
                WHERE {job_where} AND p.phase_name = $1 AND p.status = 'done'
                  AND COALESCE(p.output_md,'') <> ''
            """
            r = await conn.fetchrow(q, phase)
            total = r["total"] or 0
            major = r["major"] or 0
            out[f"{cohort}:{phase}"] = {
                "major_shipped": major, "total_done": total,
                "prevalence": round(major / total, 4) if total else None,
            }
    return out


# ── fidelity-failure classification ──────────────────────────────────────────
def is_fidelity_failure(w: str) -> bool:
    low = w.lower()
    keys = ("source-fidelity", "lesson context", "contradict", "absent from",
            "not found in", "preserve every term", "invented", "factual claim about the world")
    return any(k in low for k in keys)


def is_contradiction_major(w: str) -> bool:
    """A [major] failure that is specifically a SOURCE-FIDELITY CONTRADICTION — the
    exact trigger Task 1's rule governs ("raise a `major` failure for any factual
    claim ... that CONTRADICTS the LESSON CONTEXT"). Distinct from the contract's
    orthogonal coverage/derivation majors ("derive from lesson_context", "cover the
    specific terms") and structural majors (deck size, card format), which the judge
    raises regardless of the fidelity rule. The judge's genuine-contradiction failures
    literally carry the word "contradict"; pure-absence failures say "absent"/"not
    found"/"does not" and — under the NEW rule — must NOT be major."""
    return w.startswith("[major]") and "contradict" in w.lower()


def is_invented_number_flag(w: str) -> bool:
    """Any failure (major or minor) that flags a NUMBER as invented/unsourced — the
    R14 regen-tax the rule must NOT levy on generated teaching values."""
    low = w.lower()
    if not any(k in low for k in ("invented", "fabricat", "not found in", "not in the source",
                                  "made up", "no basis in")):
        return False
    return any(ch.isdigit() for ch in w)


def _major_mentions(warnings: list[str], tokens: tuple[str, ...]) -> bool:
    """True iff some [major] warning references the TARGET claim (any token, case-
    insensitive). This is the per-claim gate: it isolates the judge's verdict ON THE
    INJECTED FACT from incidental structural majors on OTHER cards (deck size, sub-skill
    spread, phase-title calque) — the confound the #113 re-gate correctly flagged when
    the probes gated on global `has_major`."""
    low = [w.lower() for w in warnings if w.startswith("[major]")]
    toks = tuple(t.lower() for t in tokens)
    return any(any(t in w for t in toks) for w in low)


def evaluate_probe(name: str, runs: list[dict], spec: dict | None = None) -> dict:
    """Probe gate — PER-CLAIM (rebuilt after the #113 re-gate). The earlier gate on
    global `has_major` was invalid: a contract-clean deck still draws incidental
    structural majors (sub-skill spread, title calque) on cards OTHER than the injected
    one, so `has_major` conflated "the judge mis-graded the target fact" with "the deck
    has some unrelated nit". Each probe now gates on the judge's verdict ABOUT ITS OWN
    injected claim, via `spec['gate']` = {'tokens': (...), 'expect': 'major_every_run'|
    'never_major', 'kind': 'contradiction'|'any_major'}. p3 keeps its orthogonal
    invented-number gate. Full contradiction/structural/has_major counts stay recorded
    for transparency."""
    if not runs:
        return {"pass": False, "reason": "no runs (budget/skip)"}
    contra = [any(is_contradiction_major(w) for w in r["warnings"]) for r in runs]
    invnum = [any(is_invented_number_flag(w) for w in r["warnings"]) for r in runs]
    hm = [bool(r["has_major"]) for r in runs]
    struct = [[w for w in r["warnings"] if w.startswith("[major]") and not is_contradiction_major(w)]
              for r in runs]
    base = {"contradiction_major_each_run": contra, "invented_number_flag_each_run": invnum,
            "has_major_each_run": hm,
            "structural_major_count_each_run": [len(s) for s in struct]}

    if name == "p3_generated_values":
        # R14 guard: the specific concern is generated NUMBERS flagged invented/unsourced.
        base["pass"] = not any(invnum)
        return base

    gate = (spec or {}).get("gate") or {}
    tokens = tuple(gate.get("tokens", ()))
    kind = gate.get("kind", "any_major")
    expect = gate.get("expect")
    if not tokens or expect is None:
        # No per-claim gate supplied (recompute of an OLD artifact): fall back to the
        # legacy has_major gate, clearly flagged so a reader knows it is not per-claim.
        legacy = all(hm) if name in ("p1_contradiction", "p4_genuine_defect") else not any(hm)
        base["pass"] = legacy
        base["gate_mode"] = "legacy_has_major (no per-claim spec)"
        return base

    if kind == "contradiction":
        target = [any(is_contradiction_major(w) and _major_mentions([w], tokens) for w in r["warnings"])
                  for r in runs]
    else:
        target = [_major_mentions(r["warnings"], tokens) for r in runs]
    base["target_claim_major_each_run"] = target
    base["gate_mode"] = f"per-claim tokens={tokens} kind={kind} expect={expect}"
    base["pass"] = all(target) if expect == "major_every_run" else not any(target)
    return base


P4_SELECTION_CRITERION = (
    "Defect 4, second part (merge-gate finding): the p4 'genuine defect' pick must be "
    "an UNAMBIGUOUS factual contradiction against the extract — a wrong number, name, "
    "or date verifiable in one line — never a subtle superlative or relative-ranking "
    "claim (e.g. 'the most important crop'), which a judge can reasonably read as an "
    "interpretive/emphasis claim rather than a hard factual error, and which is the "
    "kind of pick that produced the earlier stochastic-looking demotions this probe "
    "exists to rule out. Re-selection against this criterion happens at the NEXT "
    "approved run (no model call is made here to pick a replacement)."
)


def classify_residual_major(w: str) -> str:
    low = w.lower()
    if any(k in low for k in ("hint", "leak", "spoiler", "conceal", "reveal",
                              "no-spoiler", "answer a checkpoint", "depict or name")):
        return "concealment-rule"
    if any(k in low for k in ("deck size", "band", "hard cap", "maximum", "count the cards",
                              "oversized", "exceeds", "cards)", "10-12 cards", "too many cards")):
        return "deck-size"
    if is_fidelity_failure(w):
        return "source-fidelity"
    return "other"


# ── consolidation + downstream analysis (module-level so --recompute-only reuses ──
# the EXACT same code as the live run — no drift between "how the artifact was built"
# and "how it's recomputed").
def consolidated_hm(it: dict, arm: str) -> bool | None:
    """Defect 1 fix: for an item with a discordant replay, `it["arms"][arm]` holds 4
    recorded runs (1 original + 3 replay). The consolidated verdict is the MAJORITY of
    the 3 REPLAY runs ONLY (odd count -> no ties possible); the original run stays
    recorded in raw_verdicts but does not vote. An item with no replay has exactly 1
    recorded run in this arm, which stands as-is (nothing to consolidate).

    The OLD (rejected) method voted over all 4 runs, where a 2-2 split resolved to
    `majors * 2 > len(runs)` = False ("clean") — a silent tie-break toward the wrong
    answer for exactly the discordant cases this replay mechanism exists to resolve.
    """
    runs = [r for r in it["arms"][arm] if r.get("has_major") is not None]
    if not runs:
        return None
    vote_runs = runs[-REPLAY_RUNS:] if len(runs) > REPLAY_RUNS else runs
    majors = sum(1 for r in vote_runs if r["has_major"])
    return majors * 2 > len(vote_runs)  # majority; odd count when replayed => no tie


def build_transitions(items: list[dict], pair_arms: tuple[str, str], key_fn) -> dict:
    tbl: dict = {}
    for it in items:
        a = consolidated_hm(it, pair_arms[0])
        b = consolidated_hm(it, pair_arms[1])
        if a is None or b is None:
            continue
        k = key_fn(it)
        d = tbl.setdefault(k, {"stayed_major": 0, "demoted": 0, "promoted": 0, "stayed_clean": 0, "n": 0})
        d["n"] += 1
        if a and b:
            d["stayed_major"] += 1
        elif a and not b:
            d["demoted"] += 1
        elif not a and b:
            d["promoted"] += 1
        else:
            d["stayed_clean"] += 1
    return tbl


def build_transition_tables(items: list[dict]) -> dict:
    """Paired transition tables per phase (A->B, A->C), pooled cohorts + per cohort."""
    return {
        "A_to_B": {
            "per_phase": build_transitions(items, ("A", "B"), lambda it: it["phase"]),
            "per_phase_cohort": build_transitions(items, ("A", "B"), lambda it: f"{it['cohort']}:{it['phase']}"),
        },
        "A_to_C": {
            "per_phase": build_transitions(items, ("A", "C"), lambda it: it["phase"]),
            "per_phase_cohort": build_transitions(items, ("A", "C"), lambda it: f"{it['cohort']}:{it['phase']}"),
        },
    }


def cell_major_rate(items: list[dict], arm: str, cohort: str, phase: str, status: str):
    """Per-cell arm major-rate (cell = cohort:phase:status), for reweighting."""
    vals = [consolidated_hm(it, arm) for it in items
            if it["cohort"] == cohort and it["phase"] == phase and it["prior_status"] == status]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, 0
    return sum(1 for v in vals if v) / len(vals), len(vals)


def build_reweighted(items: list[dict], prev: dict) -> dict:
    reweighted = {}
    for arm in ("A", "B", "C"):
        for cohort in COHORTS:
            for phase in PHASES:
                p = prev[f"{cohort}:{phase}"]["prevalence"]
                r_major, n_major = cell_major_rate(items, arm, cohort, phase, "major")
                r_clean, n_clean = cell_major_rate(items, arm, cohort, phase, "clean")
                if p is None or r_major is None or r_clean is None:
                    est = None
                else:
                    est = round(p * r_major + (1 - p) * r_clean, 4)
                reweighted[f"{arm}:{cohort}:{phase}"] = {
                    "prevalence": p,
                    "rate_major_cell": None if r_major is None else round(r_major, 4),
                    "rate_clean_cell": None if r_clean is None else round(r_clean, 4),
                    "reweighted_major_rate": est,
                    "n_major": n_major, "n_clean": n_clean,
                }
    return reweighted


def build_residual_armC(items: list[dict]) -> dict:
    """Residual-major breakdown for arm C (first run per item, majors only). Uses the
    raw first C run's has_major (not the consolidated verdict) — unaffected by the
    Defect-1 consolidation fix, recomputed here only for reproducibility."""
    residual: dict = {}
    for it in items:
        runs = it["arms"]["C"]
        if not runs:
            continue
        r0 = runs[0]
        if not r0.get("has_major"):
            continue
        for w in r0["warnings"]:
            if not w.startswith("[major]"):
                continue
            cat = classify_residual_major(w)
            d = residual.setdefault(it["phase"], {"concealment-rule": 0, "deck-size": 0,
                                                   "source-fidelity": 0, "other": 0})
            d[cat] += 1
    return residual


# ── behavioral safety probes ─────────────────────────────────────────────────
# The probes isolate the SOURCE-FIDELITY CONTRADICTION behavior that Task 1's rule
# governs. Defect 4 fix (merge-gate finding): the probe decks must be genuinely
# CONTRACT-CLEAN — not just "well-formed" in the loose sense the earlier version
# claimed — so that `has_major` itself cleanly reflects fidelity and the probes can
# gate on it directly, per the plan. The earlier decks were 9 uniform
# `question_answer` cards with no `misconception` card; for a G7-8 lesson the
# flashcards contract REQUIRES one misconception card in the 8-10 band, so those
# decks earned a genuine structural major (missing misconception card) on top of
# whatever the fidelity rule did — gating on `is_contradiction_major`/raw
# `has_major` conflated the two, so the gate was quietly rewritten to
# `is_contradiction_major`/`is_invented_number_flag` as a harness workaround. Fixed
# here: `_GEO_FAITHFUL_CARDS` is now 10 cards (9 facts + 1 tagged `misconception`,
# fits the G7-8 8-10 band with the required card present), spans >=3 distinct
# sub-skills (region composition; resource-deposit locations; industrial output
# values; water/irrigation infrastructure), and varies `type` across
# definition/term_to_meaning/question_answer/misconception. NOTE: keeping the decks
# clean of structural nits was the FIRST attempt to make `has_major` a valid gate;
# the #113 re-gate showed that insufficient (a clean deck still draws incidental
# structural majors on cards OTHER than the injected one), so the gate is now
# PER-CLAIM (see `evaluate_probe`): each probe is judged on the verdict about its OWN
# injected fact, and these deck-quality efforts merely reduce (not eliminate) the
# incidental majors the per-claim gate already ignores. p3 keeps its narrower
# `is_invented_number_flag` gate because that probe's concern is specifically
# whether GENERATED practice numbers get regen-taxed as invented facts. Full
# contradiction / invented-number / structural counts are recorded per run.
_GEO_GRADE_PREFIX = "Grade: 8\n\n"

# Ten cards drawn straight from the Zarafshon-region extract (job 5c86f22f): region
# composition, gas/oil/gold/rare-metal deposits, the Qorovulbozor refinery capacity
# (5 mln t/yr), the Navoiy TPP fuel, the #2 gas-industry rank, the irrigation canals,
# and one tagged `misconception` card. Grade 8 -> G7-8 band is 8-10 cards INCLUDING
# one misconception card (flashcards.md: "G7-8 -> 8-10 cards — core atoms plus one
# misconception card") — 10 fits exactly and satisfies the requirement, closing the
# structural-major confound Defect 4 flagged (missing misconception card). Sub-skill
# spread (>=3, per flashcards.md): (1) region composition/administration, (2)
# resource-deposit locations (gas/oil/gold/rare metals), (3) industrial output
# values (refinery capacity, TPP fuel, gas-industry rank), (4) water/irrigation
# infrastructure. `type` varies across definition/term_to_meaning/question_answer/
# misconception rather than one uniform type for all 9 facts.
_GEO_FAITHFUL_CARDS = [
    ("card_1", "Zarafshon iqtisodiy rayoni tarkibi", "Samarqand, Buxoro va Navoiy viloyatlari.", "definition", "easy"),
    ("card_2", "Rayondagi tabiiy gaz konlari", "Gazli, Uchqir, Qorovulbozor va Sortosh.", "term_to_meaning", "medium"),
    ("card_3", "Rayondagi neft koni", "Kogon yaqinida joylashgan.", "question_answer", "medium"),
    ("card_4", "Rayondagi oltin koni", "Muruntov.", "question_answer", "easy"),
    ("card_5", "Rayondagi nodir metall konlari", "Ingichka va Zarmitan.", "term_to_meaning", "medium"),
    ("card_6", "Qorovulbozor neftni qayta ishlash korxonasi quvvati", "Yiliga 5 mln tonna neftni qayta ishlaydi.", "question_answer", "medium"),
    ("card_7", "Navoiy IESi qanday yoqilg'ida ishlaydi", "Tabiiy gaz bilan ishlaydi.", "question_answer", "easy"),
    ("card_8", "Rayonning gaz sanoati bo'yicha o'rni",
     "O'zbekistonda ikkinchi o'rinda (Sho'rtan gaz koni ishga tushirilgach).", "question_answer", "medium"),
    ("card_9", "Rayonning suv muammosini hal qiluvchi inshootlar", "Amu–Qorako'l va Amu–Buxoro kanallari, Quyimozor suv ombori.", "question_answer", "hard"),
    ("card_10", "Keng tarqalgan xato: Zarafshon rayoni faqat qishloq xo'jaligi rayoni",
     "Noto'g'ri — rayon gaz, neft, oltin va nodir metall sanoatiga ham ega og'ir sanoat rayonidir.",
     "misconception", "medium", ("O'quvchilar rayonni faqat qishloq xo'jaligi bilan bog'laydi, sanoat tarmoqlarini unutadi.", "inferred")),
]


def _fmt_deck(title: str, cards: list[tuple]) -> str:
    out = [f"# {title}\n"]
    for card in cards:
        cid, front, back, ctype, diff = card[:5]
        block = (f"**id:** {cid}\n**front:** {front}\n**back:** {back}\n"
                 f"**type:** {ctype}\n**difficulty:** {diff}\n")
        if len(card) > 5 and card[5] is not None:
            misc_text, provenance = card[5]
            block += f"**misconception ({provenance}):** {misc_text}\n"
        out.append(block)
    return "\n".join(out)


async def build_probes(conn, items: list[dict]) -> dict:
    """Construct the 4 probe cases against REAL stored extracts. Returns a dict with
    each probe's inputs + the genuine-defect selection provenance."""
    geo_job = "5c86f22f-1079-49b1-9074-cdc95b5a90f3"
    geo_ext_raw = await conn.fetchval(
        "SELECT output_md FROM phase_outputs WHERE job_id=$1 AND phase_name='extract'", geo_job)
    geo_ext = _GEO_GRADE_PREFIX + geo_ext_raw
    geo_subject, geo_ol = "geografiya", "uz"

    # p3 is pinned to a SPECIFIC real math extract (G5 division-by-10/100/1000) so the
    # probe deck can be authored ON-TOPIC — an off-topic deck earns a derivation
    # major whose text quotes the rule's own "...CONTRADICTS..." boilerplate, which is
    # not a genuine fidelity flag. Pinning removes that confound.
    p3_math_job = "d36145b8-1122-41e7-ac0d-80104bec867c"
    p3_ext_raw = await conn.fetchval(
        "SELECT output_md FROM phase_outputs WHERE job_id=$1 AND phase_name='extract'", p3_math_job)

    # Probe 4 — a CONSTRUCTED, unambiguously-wrong homework fact (rebuilt after the #113
    # re-gate rejected THREE real-stored picks). The gate's decisive objection: a real
    # stored "defect" proved only "the judge follows its extract", never "a genuinely
    # WRONG homework fact stays major", because every real numeric contradiction I found
    # was one of: a distractor (meant to be wrong), an absent-type claim (the new rule
    # demotes by design), or a case where the OUTPUT was actually correct and the
    # EXTRACT wrong (the range 1≤a<10 pick — see rejected_real_picks). A constructed
    # probe removes that ambiguity: the same faithful deck, but card_4 swaps the gold
    # deposit to KOGON — the extract explicitly assigns Kogon to the OIL deposit
    # ("Rayondagi neft koni: Kogon yaqinida") and Muruntov to gold ("oltin koni:
    # Muruntov"). So "oltin koni: Kogon" DIRECTLY CONTRADICTS the source AND is
    # objectively false (Muruntov is the world-famous gold mine). Stated as a flashcard
    # back = asserted true. This is precisely "a genuinely wrong homework fact" and it
    # must stay a contradiction-major every run. Corroborating REAL production instance
    # recorded in provenance (does not gate).
    p4_cards = list(_GEO_FAITHFUL_CARDS)
    p4_cards[3] = ("card_4", "Rayondagi oltin koni", "Kogon yaqinida joylashgan.",
                   "question_answer", "easy")
    p4_out = _fmt_deck("Flesh-kartlar — Zarafshon iqtisodiy rayoni", p4_cards)
    p4_ext = geo_ext

    # Probe 1 — direct CONTRADICTION: same faithful deck, but card_6 changes the
    # refinery capacity 5 mln t/yr -> 50 mln t/yr (a 10x changed statistic that the
    # extract directly contradicts). Must raise a contradiction-major every run.
    p1_cards = list(_GEO_FAITHFUL_CARDS)
    p1_cards[5] = ("card_6", "Qorovulbozor neftni qayta ishlash korxonasi quvvati",
                   "Yiliga 50 mln tonna neftni qayta ishlaydi.", "question_answer", "medium")
    p1_out = _fmt_deck("Flesh-kartlar — Zarafshon iqtisodiy rayoni", p1_cards)

    # Probe 2 — an ABSENT-but-true, UNCONTESTED supporting fact: same faithful deck,
    # but card_9 is replaced by a true supporting-context card (Samarqand is an
    # ancient cultural centre of the Zarafshon valley) that is NOT in the extract and
    # does NOT contradict it. Under the NEW rule this is at most minor — it must NOT
    # raise a contradiction-major.
    p2_cards = list(_GEO_FAITHFUL_CARDS)
    p2_cards[8] = ("card_9", "Samarqand shahri haqida",
                   "Samarqand — Zarafshon vodiysida joylashgan qadimiy shahar va O'zbekistonning muhim madaniy markazi.",
                   "question_answer", "easy")
    p2_out = _fmt_deck("Flesh-kartlar — Zarafshon iqtisodiy rayoni", p2_cards)

    # Probe 3 — GENERATED exercise values: an ON-TOPIC, well-formed G5 deck (band 6-8,
    # incl. a misconception card) derived from the division-by-10/100/1000 extract,
    # whose `example` fields carry generated worked-example numbers (some adjacent to,
    # some matching the extract's worked-example types). The rule must NOT flag these
    # generated teaching numbers as invented/unsourced facts (the R14 regen-tax guard).
    p3_out = (
        "# Flesh-kartlar — Sonlarni 10, 100, 1000 ga bo'lish\n\n"
        "**id:** card_1\n**front:** Sonni 10 ga bo'lish qonuniyati\n"
        "**back:** Sonning oxiridagi nol(lar)dan bittasi o'chiriladi.\n"
        "**type:** definition\n**difficulty:** easy\n**example:** 480 ÷ 10 = 48\n\n"
        "**id:** card_2\n**front:** Sonni 100 ga bo'lish qonuniyati\n"
        "**back:** Sonning oxiridagi nol(lar)dan ikkitasi o'chiriladi.\n"
        "**type:** definition\n**difficulty:** easy\n**example:** 3600 ÷ 100 = 36\n\n"
        "**id:** card_3\n**front:** Sonni 1000 ga bo'lish qonuniyati\n"
        "**back:** Sonning oxiridagi nol(lar)dan uchtasi o'chiriladi.\n"
        "**type:** definition\n**difficulty:** medium\n**example:** 45000 ÷ 1000 = 45\n\n"
        "**id:** card_4\n**front:** Sonni o'nliklarga bo'lish usuli\n"
        "**back:** Avval 10 ga bo'lib, so'ng qolgan bir xonali songa bo'linadi.\n"
        "**type:** process_step\n**difficulty:** medium\n**example:** 540 ÷ 60 = 9\n\n"
        "**id:** card_5\n**front:** Sonni yuzliklarga bo'lish usuli\n"
        "**back:** Avval 100 ga bo'lib, so'ng qolgan bir xonali songa bo'linadi.\n"
        "**type:** process_step\n**difficulty:** medium\n**example:** 4800 ÷ 600 = 8\n\n"
        "**id:** card_6\n**front:** Chamalash orqali tekshirish\n"
        "**back:** Bo'lish natijasining maqbulligini chamalash orqali baholash.\n"
        "**type:** definition\n**difficulty:** medium\n\n"
        "**id:** card_7\n**front:** Keng tarqalgan xatolik: 10 ga bo'lish\n"
        "**back:** Ba'zi o'quvchilar nol o'chirish o'rniga nol qo'shib yuboradi.\n"
        "**type:** misconception\n**difficulty:** easy\n"
    )
    p3_ext = "Grade: 5\n\n" + (p3_ext_raw or "")

    return {
        "p1_contradiction": {
            "subject": geo_subject, "phase": "flashcards", "output_language": geo_ol,
            "lesson_context": geo_ext, "output_md": p1_out,
            "expect": "contradiction-major ON THE PLANTED CARD every run (50 mln vs source 5 mln)",
            # per-claim gate: a contradiction-major that references the corrupted
            # refinery-capacity card (card_6 / '50 mln'), not any deck-wide major.
            "gate": {"tokens": ("50 mln", "qorovulbozor"), "kind": "contradiction",
                     "expect": "major_every_run"},
        },
        "p2_absent_true_fact": {
            "subject": geo_subject, "phase": "flashcards", "output_language": geo_ol,
            "lesson_context": geo_ext, "output_md": p2_out,
            "expect": "the injected absent-but-true Samarqand fact is NEVER a major (<= minor) any run",
            # per-claim gate (the #113 re-gate's own diagnosis: this fact was correctly
            # graded MINOR 3/3; the earlier has_major gate wrongly failed on incidental
            # structural/other-card majors). Gate ONLY on the injected claim's tokens.
            "gate": {"tokens": ("madaniy markaz", "qadimiy shahar"), "kind": "any_major",
                     "expect": "never_major"},
        },
        "p3_generated_values": {
            "subject": "matematika", "phase": "flashcards", "output_language": "uz",
            "lesson_context": p3_ext, "output_md": p3_out,
            "expect": "no invented-number fidelity flag any run (generated example numbers not regen-taxed)",
        },
        "p4_genuine_defect": {
            "subject": geo_subject, "phase": "flashcards", "output_language": geo_ol,
            "lesson_context": p4_ext, "output_md": p4_out,
            "expect": "contradiction-major ON THE WRONG GOLD-DEPOSIT CARD every run "
                      "(constructed: 'oltin koni: Kogon' contradicts source gold=Muruntov/oil=Kogon)",
            # per-claim gate: a contradiction-major referencing the corrupted gold card
            # (card_4 / 'oltin'), isolating the injected wrong fact from deck-wide nits.
            "gate": {"tokens": ("oltin",), "kind": "contradiction", "expect": "major_every_run"},
            "provenance": {
                "construction": "faithful Zarafshon deck with card_4 gold deposit swapped "
                                "Muruntov -> Kogon. Extract assigns Kogon to OIL ('neft koni: "
                                "Kogon yaqinida') and Muruntov to gold ('oltin koni: Muruntov'), "
                                "so 'oltin koni: Kogon' both CONTRADICTS the source and is "
                                "objectively false (Muruntov is the world's largest gold mine). "
                                "Stated as a flashcard back = asserted true.",
                "why_constructed": "the #113 re-gate correctly rejected THREE real-stored picks: "
                                   "a real numeric contradiction proves only 'the judge follows its "
                                   "extract', never 'a genuinely WRONG homework fact stays major' — "
                                   "real picks were all distractors, absent-type, or extract-side "
                                   "errors (output actually correct). A constructed wrong-fact "
                                   "removes that ambiguity.",
                "rejected_real_picks": [
                    "99b1e622 (1<=a<10 range) — output was mathematically CORRECT, extract wrong: "
                    "proves extract-following, not wrong-fact-catching (the #113 re-gate's objection)",
                    "3090f92c (tea-vs-rice) — subtle superlative/relative-ranking",
                    "53dbfbf6 (Viet roots) — stored warning hallucinated ('(2; -5)' not in output)",
                ],
                "corroborating_real_instance": "fba07ae7 (G11 uz biology flashcards card_8): a REAL "
                    "stored flashcard back altered the source 'yorug'likning kamligi' (scarcity of "
                    "light) to 'mutlaqo yo'qligi' (complete absence) — the same wrong-fact-on-a-card "
                    "failure mode this probe constructs; recorded to show it occurs in production.",
            },
        },
    }


async def run_probes(specs: dict, budget: Budget, sem: asyncio.Semaphore,
                     judge_provider: str, judge_model) -> dict:
    """Run each probe 3x through the NEW rule (production get_prompt contract)."""
    probe_results: dict = {}

    async def run_probe(name: str, spec: dict):
        runs = []
        for _ in range(3):
            async with sem:
                v = await run_one_judge(
                    budget,
                    meta={"kind": "probe", "probe": name},
                    subject=spec["subject"], phase_name=spec["phase"],
                    output_md=spec["output_md"], lesson_context=spec["lesson_context"],
                    prior_outputs={}, gen_provider=GEN_PROVIDER, gen_model=GEN_MODEL,
                    judge_provider=judge_provider, judge_model=judge_model,
                    transport=TRANSPORT, contract_override=None,
                    output_language=spec["output_language"],
                )
            if v is not None:
                runs.append(v)
        probe_results[name] = runs

    for name, spec in specs.items():
        await run_probe(name, spec)
    return probe_results


async def run_p4_baseline(spec: dict, budget: Budget, sem: asyncio.Semaphore,
                          judge_provider: str, judge_model) -> list[dict]:
    """Run the p4 constructed wrong-fact output (gold Muruntov->Kogon) under the OLD
    rule 3x, confirming the new rule does NOT demote a genuine, unambiguous contradiction
    relative to the OLD-rule baseline (it is a contradiction-major under both)."""
    old = old_fidelity_rule()
    runs: list[dict] = []
    saved = phase_judge._FIDELITY_RULE
    try:
        phase_judge._FIDELITY_RULE = old
        for _ in range(3):
            async with sem:
                v = await run_one_judge(
                    budget, meta={"kind": "p4_baseline"},
                    subject=spec["subject"], phase_name=spec["phase"], output_md=spec["output_md"],
                    lesson_context=spec["lesson_context"], prior_outputs={},
                    gen_provider=GEN_PROVIDER, gen_model=GEN_MODEL,
                    judge_provider=judge_provider, judge_model=judge_model,
                    transport=TRANSPORT, contract_override=None,
                    output_language=spec["output_language"])
            if v is not None:
                runs.append(v)
    finally:
        phase_judge._FIDELITY_RULE = saved
    return runs


async def probes_only(args) -> int:
    """Re-run ONLY the 12 safety-probe calls and patch the existing artifact's
    `safety_probes` section in place (arm data + transition/reweight analysis from
    the full run are preserved). Used after a probe-harness refinement so the arm
    calls are not needlessly re-billed."""
    prior_calls, prior_cost = load_prior_budget()
    refuse_if_cumulative_cap_hit(prior_calls, prior_cost, args.max_calls, args.max_cost_usd)
    budget = Budget(args.max_calls, args.max_cost_usd, prior_calls, prior_cost)
    sem = asyncio.Semaphore(args.concurrency)
    judge_provider, judge_model = model_tiers.resolve_judge(
        GEN_PROVIDER, GEN_MODEL, JUDGE_PROVIDER_STAMP, JUDGE_MODEL_STAMP)

    conn = await asyncpg.connect(dsn())
    try:
        specs = await build_probes(conn, [])
    finally:
        await conn.close()

    probe_results = await run_probes(specs, budget, sem, judge_provider, judge_model)
    p4_baseline = await run_p4_baseline(specs["p4_genuine_defect"], budget, sem,
                                        judge_provider, judge_model)

    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    probes_out = {}
    all_pass = True
    for name, runs in probe_results.items():
        verdict = evaluate_probe(name, runs, specs[name])
        if verdict.get("pass") is not True:
            all_pass = False
        probes_out[name] = {"expect": specs[name]["expect"], "verdict": verdict,
                            "runs": runs, "provenance": specs[name].get("provenance")}
    artifact["safety_probes"] = {
        "all_pass": all_pass,
        "gate_note": "gate is PER-CLAIM: each probe is judged on the verdict about its OWN injected "
                     "fact (p1/p4 -> that fact's contradiction-major every run; p2 -> that "
                     "fact never major), NOT deck-wide has_major, so incidental structural "
                     "majors on other cards cannot confound the result. p3 gates narrowly on "
                     "the invented-number flag. contradiction / invented-number / structural "
                     "counts + per-claim target_claim_major are recorded per run.",
        "genuine_defect_provenance": specs["p4_genuine_defect"].get("provenance"),
        "p4_baseline_comparison": {
            "note": "Same p4 defect output under the OLD rule (57b81aa) x3 vs the NEW rule x3, "
                    "confirming the new rule does NOT demote the constructed wrong-fact contradiction "
                    "(gold Muruntov->Kogon) relative to the OLD-rule baseline; major under both.",
            "old_rule_has_major": [bool(r["has_major"]) for r in p4_baseline],
            "old_rule_contradiction_major": [any(is_contradiction_major(w) for w in r["warnings"]) for r in p4_baseline],
            "new_rule_has_major": probes_out["p4_genuine_defect"]["verdict"]["has_major_each_run"],
            "new_rule_contradiction_major": probes_out["p4_genuine_defect"]["verdict"]["contradiction_major_each_run"],
        },
        "probes": probes_out,
        "reran_at": datetime.now(timezone.utc).isoformat(),
    }
    b = artifact.setdefault("budget", {})
    # budget.calls / budget.cost are CUMULATIVE (seeded from the artifact's prior
    # calls_made/actual_cost_usd) — write them straight through, don't add again.
    b["max_calls"] = args.max_calls
    b["max_cost_usd"] = args.max_cost_usd
    b["probe_rerun_calls"] = budget.new_calls
    b["probe_rerun_cost_usd"] = round(budget.new_cost, 6)
    b["calls_made"] = budget.calls
    b["actual_cost_usd"] = round(budget.cost, 6)
    b["budget_hit"] = budget.hit
    artifact["note_probe_rerun"] = ("Arm calls from the full run are unchanged; the safety "
                                    "probes were re-run after refining the probe harness "
                                    "(well-formed grade-pinned decks + has_major gate). "
                                    "The fidelity RULE was NOT modified.")
    ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"probes-only: calls={budget.new_calls} cost=${budget.new_cost:.4f} "
          f"(cumulative calls={budget.calls} cost=${budget.cost:.4f}) all_pass={all_pass}")
    for name, po in probes_out.items():
        print(f"  {name}: pass={po['verdict'].get('pass')} "
              f"contra={po['verdict'].get('contradiction_major_each_run')} "
              f"hm={po['verdict'].get('has_major_each_run')}")
    return 0 if all_pass else 2


def recompute_only() -> int:
    """--recompute-only: ZERO model calls, ZERO DB connections. Re-derives consolidated
    verdicts, transition tables, reweighted population rates, and the residual
    breakdown FROM THE RAW VERDICTS already recorded in the on-disk artifact, using
    the corrected Defect-1 consolidation (`consolidated_hm`: majority of the 3 REPLAY
    runs only; the original run stays recorded but does not vote). Updates the
    artifact in place and appends a `corrections` entry recording what changed and
    the before/after headline numbers.
    """
    if not ARTIFACT_PATH.exists():
        print(f"no artifact at {ARTIFACT_PATH} — nothing to recompute", file=sys.stderr)
        return 1

    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    raw = artifact.get("raw_verdicts")
    prev = artifact.get("prevalence")
    if not raw or not prev:
        print("artifact is missing raw_verdicts/prevalence — cannot recompute", file=sys.stderr)
        return 1

    # Rebuild lightweight item records straight from the recorded raw verdicts —
    # every run (original + replays) is already there; nothing is re-fetched or re-run.
    items: list[dict] = []
    before_consolidated: dict[int, dict] = {}
    for idx_str, rv in raw.items():
        idx = int(idx_str)
        items.append({
            "item_index": idx, "cohort": rv["cohort"], "phase": rv["phase"],
            "prior_status": rv["prior_status"], "phase_output_id": rv["phase_output_id"],
            "arms": {"A": rv["A"], "B": rv["B"], "C": rv["C"]},
        })
        before_consolidated[idx] = dict(rv.get("consolidated") or {})
    items.sort(key=lambda it: it["item_index"])

    # ── recompute with the corrected consolidation ────────────────────────────
    new_trans = build_transition_tables(items)
    new_reweighted = build_reweighted(items, prev)
    new_residual = build_residual_armC(items)

    changed_items = []
    for it in items:
        after = {a: consolidated_hm(it, a) for a in ("A", "B", "C")}
        before = before_consolidated.get(it["item_index"], {})
        if before != after:
            changed_items.append({
                "item_index": it["item_index"], "cohort": it["cohort"], "phase": it["phase"],
                "prior_status": it["prior_status"], "phase_output_id": it["phase_output_id"],
                "before": before, "after": after,
            })
        raw[str(it["item_index"])]["consolidated"] = after

    before_reweighted = artifact.get("reweighted_population_rates", {})
    before_trans = artifact.get("transition_tables", {})
    before_residual = artifact.get("residual_major_breakdown_armC", {})

    artifact["raw_verdicts"] = raw
    artifact["transition_tables"] = new_trans
    artifact["reweighted_population_rates"] = new_reweighted
    artifact["residual_major_breakdown_armC"] = new_residual
    artifact["consolidation_method"] = (
        "3-replay-majority (Defect 1 fix, gate finding 2): for an item with a "
        "discordant replay (4 recorded runs: 1 original + 3 replay), the consolidated "
        "has_major is the majority of the 3 REPLAY runs ONLY (odd count -> no ties "
        "possible); the original run stays recorded in raw_verdicts but does not "
        "vote. An item with no replay uses its single recorded run as-is. Superseded "
        "method: majority over all 4 runs, where a 2-2 tie resolved to False (clean)."
    )
    corrections = artifact.setdefault("corrections", [])
    corrections.append({
        "date": datetime.now(timezone.utc).isoformat(),
        "reason": "gate finding 2: 3-replay-majority consolidation (was 4-vote with tie→clean)",
        "changed_items": changed_items,
        "before": {
            "reweighted_population_rates": before_reweighted,
            "transition_tables": before_trans,
            "residual_major_breakdown_armC": before_residual,
        },
        "after": {
            "reweighted_population_rates": new_reweighted,
            "transition_tables": new_trans,
            "residual_major_breakdown_armC": new_residual,
        },
    })

    ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 70)
    print("recompute-only: ZERO model calls. Corrected 3-replay-majority consolidation.")
    print(f"changed items ({len(changed_items)}):")
    for c in changed_items:
        print(f"  item {c['item_index']} ({c['cohort']}:{c['phase']}:{c['prior_status']}) "
              f"before={c['before']} after={c['after']}")
    print("-" * 70)
    print("reweighted_population_rates (arm C), before -> after:")
    for key in sorted(k for k in new_reweighted if k.startswith("C:")):
        b = before_reweighted.get(key, {}).get("reweighted_major_rate")
        a = new_reweighted[key]["reweighted_major_rate"]
        flag = "  <-- CHANGED" if b != a else ""
        print(f"  {key}: {b} -> {a}{flag}")
    print("-" * 70)
    print("A_to_C per_phase_cohort, before -> after:")
    for key in sorted(new_trans["A_to_C"]["per_phase_cohort"]):
        print(f"  {key}: before={before_trans.get('A_to_C', {}).get('per_phase_cohort', {}).get(key)} "
              f"after={new_trans['A_to_C']['per_phase_cohort'][key]}")
    print("=" * 70)
    return 0


# ── main orchestration ───────────────────────────────────────────────────────
async def main() -> int:
    global ARTIFACT_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default=DEFAULT_SEED)
    ap.add_argument("--max-calls", type=int, default=200)
    ap.add_argument("--max-cost-usd", type=float, default=6.0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--probes-only", action="store_true",
                    help="re-run only the 12 safety-probe calls and patch the artifact")
    ap.add_argument("--recompute-only", action="store_true",
                    help="ZERO model calls: re-derive consolidated verdicts / transition "
                         "tables / reweighted rates / residual breakdown from the raw "
                         "verdicts already in the artifact, using the corrected Defect-1 "
                         "(3-replay-majority) consolidation")
    ap.add_argument("--artifact", type=str, default=None,
                    help="override the artifact path (default: "
                         f"{ARTIFACT_PATH}); lets a corrected re-run write to a FRESH "
                         "file, leaving a superseded artifact untouched. The "
                         "cumulative-budget gate (Defect 3) keys off whichever "
                         "artifact path is in effect, so a new path starts a clean "
                         "budget by design.")
    args = ap.parse_args()

    if args.artifact:
        ARTIFACT_PATH = Path(args.artifact)

    if args.recompute_only:
        return recompute_only()

    if args.probes_only:
        return await probes_only(args)

    prior_calls, prior_cost = load_prior_budget()
    refuse_if_cumulative_cap_hit(prior_calls, prior_cost, args.max_calls, args.max_cost_usd)
    budget = Budget(args.max_calls, args.max_cost_usd, prior_calls, prior_cost)
    sem = asyncio.Semaphore(args.concurrency)

    judge_provider, judge_model = model_tiers.resolve_judge(
        GEN_PROVIDER, GEN_MODEL, JUDGE_PROVIDER_STAMP, JUDGE_MODEL_STAMP)

    old_rule = old_fidelity_rule()
    new_rule = phase_judge._FIDELITY_RULE  # live module default

    conn = await asyncpg.connect(dsn())
    try:
        items = await sample_cells(conn, args.seed)
        prev = await prevalence(conn)
        probes_spec = await build_probes(conn, items)
    finally:
        await conn.close()

    # arm -> per-item verdict lists
    for it in items:
        it["arms"] = {"A": [], "B": [], "C": []}

    async def run_arm_call(it: dict, arm: str) -> None:
        """One judge call for one (item, arm). FULLY SEQUENTIAL by construction — the
        caller never awaits two of these concurrently. Arm A monkeypatches the
        process-global `phase_judge._FIDELITY_RULE` in a try/finally scoped to just
        this one call, so the global is never in the OLD state while any other call
        (any arm, any item) could observe it."""
        contract_override = (
            render_old_contract(it["subject"], it["phase"], it["output_language"])
            if arm in ("A", "B") else None
        )
        meta = {"kind": "arm", "arm": arm, "item_index": it["item_index"],
                "phase_output_id": it["phase_output_id"]}
        kwargs = dict(
            subject=it["subject"], phase_name=it["phase"], output_md=it["output_md"],
            lesson_context=it["extract_md"], prior_outputs={},
            gen_provider=GEN_PROVIDER, gen_model=GEN_MODEL,
            judge_provider=judge_provider, judge_model=judge_model,
            transport=TRANSPORT, contract_override=contract_override,
            output_language=it["output_language"],
        )
        if arm == "A":
            saved_rule = phase_judge._FIDELITY_RULE
            phase_judge._FIDELITY_RULE = old_rule
            try:
                v = await run_one_judge(budget, meta, **kwargs)
            finally:
                phase_judge._FIDELITY_RULE = saved_rule
        else:
            v = await run_one_judge(budget, meta, **kwargs)
        if v is not None:
            it["arms"][arm].append(v)

    # ─── main 3-arm pass: per-item rotated order, FULLY SEQUENTIAL ───────────
    # Counterbalancing (Defect 2 fix): rotate execution order by item_index % 3
    # instead of running grouped by arm. Sequential (no gather/semaphore) because
    # arm A's monkeypatch is a process-global with no per-call scoping — ANY
    # concurrent non-A call while it's active would read the wrong rule text.
    for it in items:
        order = ARM_ROTATION[it["item_index"] % 3]
        for arm in order:
            await run_arm_call(it, arm)

    # ─── discordant detection (A vs C first-run has_major) ───────────────────
    def first_hm(it, arm):
        runs = it["arms"][arm]
        return runs[0]["has_major"] if runs else None

    discordant = [
        it for it in items
        if first_hm(it, "A") is not None and first_hm(it, "C") is not None
        and bool(first_hm(it, "A")) != bool(first_hm(it, "C"))
    ]
    discordant.sort(key=lambda it: it["item_index"])
    replay_items = discordant[:DISCORDANT_CAP]
    not_replayed = [it["item_index"] for it in discordant[DISCORDANT_CAP:]]

    # ─── replays: A and C only (never B), rotated per item, FULLY SEQUENTIAL ──
    for it in replay_items:
        replay_order = "AC" if it["item_index"] % 2 == 0 else "CA"
        for _ in range(REPLAY_RUNS):
            for arm in replay_order:
                await run_arm_call(it, arm)

    # probes (NEW rule, no patch, production get_prompt contract)
    probe_results = await run_probes(probes_spec, budget, sem, judge_provider, judge_model)
    p4_baseline = await run_p4_baseline(probes_spec["p4_genuine_defect"], budget, sem,
                                        judge_provider, judge_model)

    # ─── analysis (shared module-level functions — same code path --recompute-only uses) ──
    trans = build_transition_tables(items)
    reweighted = build_reweighted(items, prev)
    residual = build_residual_armC(items)

    # probe verdicts
    probes_out = {}
    all_probes_pass = True
    for name, runs in probe_results.items():
        verdict = evaluate_probe(name, runs, probes_spec[name])
        if verdict.get("pass") is not True:
            all_probes_pass = False
        probes_out[name] = {
            "expect": probes_spec[name]["expect"],
            "verdict": verdict,
            "runs": runs,
            "provenance": probes_spec[name].get("provenance"),
        }

    # ─── contract / rule text hashes ─────────────────────────────────────────
    text_hashes = {
        "fidelity_rule": {
            "arm_A_old": {"sha256": sha(old_rule), "len": len(old_rule)},
            "arm_B_C_new": {"sha256": sha(new_rule), "len": len(new_rule)},
        },
        "contracts": {},
    }
    seen_contract = set()
    for it in items:
        key = f"{it['subject']}:{it['phase']}:{it['output_language']}"
        if key in seen_contract:
            continue
        seen_contract.add(key)
        oldc = render_old_contract(it["subject"], it["phase"], it["output_language"])
        newc = new_contract(it["subject"], it["phase"], it["output_language"])
        text_hashes["contracts"][key] = {
            "arm_A_B_old": {"sha256": sha(oldc), "len": len(oldc)},
            "arm_C_new": {"sha256": sha(newc), "len": len(newc)},
            "identical": sha(oldc) == sha(newc),
        }

    # ─── assemble artifact ───────────────────────────────────────────────────
    artifact = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment": "three-arm re-judge A/B + behavioral safety probes",
        "seed": args.seed,
        "branch_point_sha": BRANCH_SHA,
        "generator": {"provider": GEN_PROVIDER, "model": GEN_MODEL},
        "judge": {"provider": judge_provider, "model": judge_model,
                  "resolved_from_stamp": [JUDGE_PROVIDER_STAMP, JUDGE_MODEL_STAMP],
                  "transport": TRANSPORT},
        "arm_construction": {
            "A": "OLD _FIDELITY_RULE (57b81aa) + OLD contracts (contract_override); production baseline",
            "B": "NEW rule + OLD contracts (contract_override); isolates Task 1",
            "C": "NEW rule + NEW contracts (no override, production get_prompt); shipped state",
            "old_rule_swap": "process-global phase_judge._FIDELITY_RULE monkeypatched inside try/finally",
            "old_contract_render": "OLD md body from git 57b81aa + get_prompt-mimicking substitutions "
                                   "with the OLD language rule (no uz label clause); FAMILY_RULES + base "
                                   "language blocks are byte-identical old/new so nothing else drifts",
            "execution_order": "PER-ITEM ROTATED (item_index % 3: 0->ABC, 1->BCA, 2->CAB; replays A/C "
                               "alternate by item_index parity), FULLY SEQUENTIAL — one judge call at a "
                               "time, never gathered/concurrent. Superseded design (rejected at merge "
                               "gate): grouped-by-arm (all A, then B+C, then A-replays, then C-replays) "
                               "on the theory that stateless unseeded judge calls can't be call-order "
                               "biased; that let a systematic call-order confound ride alongside the "
                               "rule/contract manipulation. Arm A's process-global monkeypatch "
                               "(`phase_judge._FIDELITY_RULE`) is applied in a try/finally scoped to "
                               "each individual A call (`run_arm_call`), which is what makes full "
                               "sequential execution safe: the global is only ever in the OLD state for "
                               "the duration of one call.",
            "concurrency": "sequential (arm calls); probes keep semaphore-bounded concurrency "
                            f"({args.concurrency})",
        },
        "sample": {
            "per_cell": PER_CELL, "n_cells": len(COHORTS) * len(PHASES) * len(STATUSES),
            "n_items": len(items),
            "items": [
                {"item_index": it["item_index"], "cohort": it["cohort"], "phase": it["phase"],
                 "prior_status": it["prior_status"], "job_id": it["job_id"],
                 "phase_output_id": it["phase_output_id"], "subject": it["subject"],
                 "output_language": it["output_language"]}
                for it in items
            ],
        },
        "prevalence": prev,
        "text_hashes": text_hashes,
        "raw_verdicts": {
            str(it["item_index"]): {
                "cohort": it["cohort"], "phase": it["phase"], "prior_status": it["prior_status"],
                "phase_output_id": it["phase_output_id"],
                "A": it["arms"]["A"], "B": it["arms"]["B"], "C": it["arms"]["C"],
                "consolidated": {a: consolidated_hm(it, a) for a in ("A", "B", "C")},
            }
            for it in items
        },
        "discordant": {
            "n_total": len(discordant),
            "replayed_item_indices": [it["item_index"] for it in replay_items],
            "discordant_not_replayed": not_replayed,
            "cap": DISCORDANT_CAP, "replay_runs_per_arm": REPLAY_RUNS,
        },
        "transition_tables": trans,
        "reweighted_population_rates": reweighted,
        "residual_major_breakdown_armC": residual,
        "residual_major_classifier": "keyword heuristic over serialized [major] failures: concealment-rule "
                                      "(hint/leak/spoiler/no-spoiler), deck-size (band/hard cap/count/cards), "
                                      "source-fidelity (contradict/lesson context/absent from/preserve every), "
                                      "else other; first arm-C run per item, replays excluded from this tally",
        "safety_probes": {
            "all_pass": all_probes_pass,
            "gate_note": "gate is PER-CLAIM: each probe is judged on the verdict about its OWN "
                         "injected fact (p1/p4 -> that fact's contradiction-major every run; p2 -> "
                         "that fact never major), NOT deck-wide has_major, so incidental structural "
                         "majors on other cards cannot confound the result. p3 gates narrowly on the "
                         "invented-number flag. contradiction / invented-number / structural counts "
                         "+ per-claim target_claim_major are recorded per run.",
            "genuine_defect_provenance": probes_spec["p4_genuine_defect"].get("provenance"),
            "p4_baseline_comparison": {
                "note": "Same p4 defect output under the OLD rule (57b81aa) x3 vs the NEW rule x3.",
                "old_rule_has_major": [bool(r["has_major"]) for r in p4_baseline],
                "old_rule_contradiction_major": [any(is_contradiction_major(w) for w in r["warnings"]) for r in p4_baseline],
                "new_rule_has_major": probes_out["p4_genuine_defect"]["verdict"]["has_major_each_run"],
                "new_rule_contradiction_major": probes_out["p4_genuine_defect"]["verdict"]["contradiction_major_each_run"],
            },
            "probes": probes_out,
        },
        "budget": {
            "max_calls": args.max_calls, "max_cost_usd": args.max_cost_usd,
            "calls_made": budget.calls, "actual_cost_usd": round(budget.cost, 6),
            "new_calls_this_run": budget.new_calls,
            "new_cost_usd_this_run": round(budget.new_cost, 6),
            "prompt_tokens": budget.prompt_tokens, "output_tokens": budget.output_tokens,
            "cached_tokens": budget.cached_tokens,
            "budget_hit": budget.hit,
            "skipped": budget.skipped,
        },
    }

    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── console summary ──────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"artifact -> {ARTIFACT_PATH}")
    print(f"calls={budget.calls} cost=${budget.cost:.4f} budget_hit={budget.hit}")
    print("A->C per phase:", json.dumps(trans["A_to_C"]["per_phase"]))
    print("reweighted (arm C):", {k: v["reweighted_major_rate"]
                                   for k, v in reweighted.items() if k.startswith("C:")})
    print(f"safety probes all_pass={all_probes_pass}")
    for name, po in probes_out.items():
        print(f"  {name}: pass={po['verdict'].get('pass')}")
    print("=" * 70)
    return 0 if all_probes_pass else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
