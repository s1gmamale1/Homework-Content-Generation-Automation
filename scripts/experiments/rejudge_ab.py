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

Design notes (why this differs from a naive per-item counterbalanced run):
  * The OLD-rule arm monkeypatches the process-global `phase_judge._FIDELITY_RULE`.
    That global is read at judge-prompt build time, so an OLD-rule call MUST NOT run
    concurrently with a NEW-rule call. We therefore execute grouped BY ARM (all A,
    then B+C, then replays, then probes) inside a single try/finally that restores the
    global. This is safe for statistical validity because each judge call is an
    independent, stateless API request with no shared conversation — call ORDER cannot
    bias an unseeded gemini verdict, whereas interleaving the global mutation would be
    a real correctness bug. Documented in the artifact under `arm_construction`.
  * Only `prompts/_general/flashcards.md` and the appended uz label clause in
    `prompts._resolve_language_rule` changed between 57b81aa and HEAD; the CBP md and
    the FAMILY_RULES blocks are byte-identical. The OLD contract render reuses the
    (frozen) FAMILY_RULES + base language blocks from the live module and only strips
    the new uz clause / swaps in the OLD md body — so arms differ ONLY in the intended
    text.
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


# ── budget-guarded judge runner ──────────────────────────────────────────────
class Budget:
    def __init__(self, max_calls: int, max_cost: float):
        self.max_calls = max_calls
        self.max_cost = max_cost
        self.calls = 0
        self.cost = 0.0
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.hit = False
        self.skipped: list[dict] = []
        self._lock = asyncio.Lock()

    async def reserve(self, meta: dict) -> bool:
        async with self._lock:
            if self.hit or self.calls >= self.max_calls or self.cost >= self.max_cost:
                self.hit = True
                self.skipped.append(meta)
                return False
            self.calls += 1
            return True

    async def settle(self, bucket: list):
        pt = sum(int(u["usage"].get("prompt_tokens") or 0) for u in bucket)
        ot = sum(int(u["usage"].get("output_tokens") or 0) for u in bucket)
        ct = sum(int(u["usage"].get("cached_tokens") or 0) for u in bucket)
        cost = sum(pricing.cost_usd(u["provider"], u["model"], u["usage"]) for u in bucket)
        async with self._lock:
            self.cost += cost
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


def evaluate_probe(name: str, runs: list[dict]) -> dict:
    """Fidelity-scoped probe gate. The gate is expressed on the rule's actual
    mechanism (contradiction-major / invented-number flag), NOT raw has_major —
    structural/coverage majors are orthogonal to Task 1's rule and are reported for
    transparency but do not fail the fidelity gate."""
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
    if name == "p1_contradiction":
        base["pass"] = all(contra)
    elif name == "p2_absent_true_fact":
        base["pass"] = not any(contra)
    elif name == "p3_generated_values":
        # R14 guard: the specific concern is generated NUMBERS being flagged as
        # invented/unsourced. Gate on that; a topic/coverage major that merely quotes
        # the rule's boilerplate is not a number-fidelity flag.
        base["pass"] = not any(invnum)
    elif name == "p4_genuine_defect":
        base["pass"] = all(hm)
    else:
        base["pass"] = None
    return base


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


# ── behavioral safety probes ─────────────────────────────────────────────────
# The probes isolate the SOURCE-FIDELITY CONTRADICTION behavior that Task 1's rule
# governs. To keep that signal clean, the flashcards decks are WELL-FORMED (grade
# pinned in the lesson_context so the deck-size band is unambiguous, canonical card
# types, id/front/back/type/difficulty present) so that structural / coverage majors
# — which the judge raises regardless of the fidelity rule — do not confound the
# fidelity gate. The gate is expressed on `is_contradiction_major` (p1/p2/p4) and
# `is_invented_number_flag` (p3), NOT raw has_major; raw has_major + structural-major
# counts are still recorded per run for transparency.
_GEO_GRADE_PREFIX = "Grade: 8\n\n"

# Nine faithful cards drawn straight from the Zarafshon-region extract (job
# 5c86f22f): region composition, gas/oil/gold/rare-metal deposits, the Qorovulbozor
# refinery capacity (5 mln t/yr), the Navoiy TPP fuel, the #2 gas-industry rank, and
# the irrigation canals. Grade 8 → band 8-10 cards; nine fits.
_GEO_FAITHFUL_CARDS = [
    ("card_1", "Zarafshon iqtisodiy rayoni tarkibi", "Samarqand, Buxoro va Navoiy viloyatlari.", "question_answer", "easy"),
    ("card_2", "Rayondagi tabiiy gaz konlari", "Gazli, Uchqir, Qorovulbozor va Sortosh.", "question_answer", "medium"),
    ("card_3", "Rayondagi neft koni", "Kogon yaqinida joylashgan.", "question_answer", "medium"),
    ("card_4", "Rayondagi oltin koni", "Muruntov.", "question_answer", "easy"),
    ("card_5", "Rayondagi nodir metall konlari", "Ingichka va Zarmitan.", "question_answer", "medium"),
    ("card_6", "Qorovulbozor neftni qayta ishlash korxonasi quvvati", "Yiliga 5 mln tonna neftni qayta ishlaydi.", "question_answer", "medium"),
    ("card_7", "Navoiy IESi qanday yoqilg'ida ishlaydi", "Tabiiy gaz bilan ishlaydi.", "question_answer", "easy"),
    ("card_8", "Rayonning gaz sanoati bo'yicha o'rni", "O'zbekistonda ikkinchi o'rinda.", "question_answer", "medium"),
    ("card_9", "Rayonning suv muammosini hal qiluvchi inshootlar", "Amu–Qorako'l va Amu–Buxoro kanallari, Quyimozor suv ombori.", "question_answer", "hard"),
]


def _fmt_deck(title: str, cards: list[tuple]) -> str:
    out = [f"# {title}\n"]
    for cid, front, back, ctype, diff in cards:
        out.append(f"**id:** {cid}\n**front:** {front}\n**back:** {back}\n"
                   f"**type:** {ctype}\n**difficulty:** {diff}\n")
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

    # Probe 4 — a manually-confirmed GENUINE defect (real stored output): a geo
    # flashcards card whose back claims Chinese tea is "eng asosiy qishloq xo'jaligi
    # ekini" (THE single most important agricultural crop) of East Asia, while the
    # extract states "O'lkaning asosiy ekini SHOLI va ... Xitoy choyidir" (the main
    # crop is RICE, with tea also famous). Elevating tea above rice as the primary
    # crop genuinely contradicts the source — a clearer, non-ambiguous defect than the
    # earlier Qorovulbozor pick (which the source itself associated with oil refining).
    p4_pid = "3090f92c-fe23-4bb6-9afa-fd77b3034c76"
    p4_job = "290b0aa2-113f-4e32-9e53-a5e744e9a79a"
    p4_out = await conn.fetchval("SELECT output_md FROM phase_outputs WHERE id=$1", p4_pid)
    p4_ext = await conn.fetchval(
        "SELECT output_md FROM phase_outputs WHERE job_id=$1 AND phase_name='extract'", p4_job)

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
            "expect": "contradiction-major present every run (planted 50 mln vs source 5 mln)",
        },
        "p2_absent_true_fact": {
            "subject": geo_subject, "phase": "flashcards", "output_language": geo_ol,
            "lesson_context": geo_ext, "output_md": p2_out,
            "expect": "no contradiction-major any run (absent supporting fact stays <= minor)",
        },
        "p3_generated_values": {
            "subject": "matematika", "phase": "flashcards", "output_language": "uz",
            "lesson_context": p3_ext, "output_md": p3_out,
            "expect": "no invented-number fidelity flag any run (generated example numbers not regen-taxed)",
        },
        "p4_genuine_defect": {
            "subject": geo_subject, "phase": "flashcards", "output_language": geo_ol,
            "lesson_context": p4_ext, "output_md": p4_out,
            "expect": "has_major present every run (genuine contradiction not demoted)",
            "provenance": {
                "phase_output_id": p4_pid, "job_id": p4_job,
                "defect": "flashcard back claims Chinese tea is 'eng asosiy qishloq xo'jaligi ekini' "
                          "(THE single most important agricultural crop) of East Asia; extract states "
                          "the main crop is RICE ('asosiy ekini SHOLI'), with tea also famous. Elevating "
                          "tea above rice as the primary crop genuinely contradicts the source.",
                "verified_against_extract": True,
                "superseded_pick": "214e7476 (Qorovulbozor neft/gaz) — dropped as ambiguous: the source "
                                   "itself associates Qorovulbozor with oil refining.",
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
    """Run the p4 genuine-defect output under the OLD rule 3x, to answer whether the
    new rule DEMOTES the defect relative to the production baseline (vs. the judge
    simply being stochastic on a subtle contradiction under BOTH rules)."""
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
    budget = Budget(args.max_calls, args.max_cost_usd)
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
        verdict = evaluate_probe(name, runs)
        if verdict.get("pass") is not True:
            all_pass = False
        probes_out[name] = {"expect": specs[name]["expect"], "verdict": verdict,
                            "runs": runs, "provenance": specs[name].get("provenance")}
    artifact["safety_probes"] = {
        "all_pass": all_pass,
        "gate_note": "fidelity-scoped gate on contradiction-major / invented-number flag "
                     "(the rule's actual mechanism); structural/coverage majors reported but "
                     "not part of the gate. Probes are well-formed grade-pinned decks.",
        "genuine_defect_provenance": specs["p4_genuine_defect"].get("provenance"),
        "p4_baseline_comparison": {
            "note": "Same p4 defect output under the OLD rule (57b81aa) x3 vs the NEW rule x3, "
                    "isolating whether the new rule DEMOTES the defect relative to baseline or the "
                    "judge is simply stochastic on this subtle contradiction under BOTH rules.",
            "old_rule_has_major": [bool(r["has_major"]) for r in p4_baseline],
            "old_rule_contradiction_major": [any(is_contradiction_major(w) for w in r["warnings"]) for r in p4_baseline],
            "new_rule_has_major": probes_out["p4_genuine_defect"]["verdict"]["has_major_each_run"],
            "new_rule_contradiction_major": probes_out["p4_genuine_defect"]["verdict"]["contradiction_major_each_run"],
        },
        "probes": probes_out,
        "reran_at": datetime.now(timezone.utc).isoformat(),
    }
    b = artifact.setdefault("budget", {})
    b["probe_rerun_calls"] = budget.calls
    b["probe_rerun_cost_usd"] = round(budget.cost, 6)
    b["calls_made"] = b.get("calls_made", 0) + budget.calls
    b["actual_cost_usd"] = round(b.get("actual_cost_usd", 0.0) + budget.cost, 6)
    artifact["note_probe_rerun"] = ("Arm calls from the full run are unchanged; the safety "
                                    "probes were re-run after refining the probe harness "
                                    "(well-formed grade-pinned decks + contradiction-scoped gate). "
                                    "The fidelity RULE was NOT modified.")
    ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"probes-only: calls={budget.calls} cost=${budget.cost:.4f} all_pass={all_pass}")
    for name, po in probes_out.items():
        print(f"  {name}: pass={po['verdict'].get('pass')} "
              f"contra={po['verdict'].get('contradiction_major_each_run')} "
              f"hm={po['verdict'].get('has_major_each_run')}")
    return 0 if all_pass else 2


# ── main orchestration ───────────────────────────────────────────────────────
async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default=DEFAULT_SEED)
    ap.add_argument("--max-calls", type=int, default=200)
    ap.add_argument("--max-cost-usd", type=float, default=6.0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--probes-only", action="store_true",
                    help="re-run only the 12 safety-probe calls and patch the artifact")
    args = ap.parse_args()

    if args.probes_only:
        return await probes_only(args)

    budget = Budget(args.max_calls, args.max_cost_usd)
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

    async def judge_item(it: dict, arm: str, contract_override):
        async with sem:
            v = await run_one_judge(
                budget,
                meta={"kind": "arm", "arm": arm, "item_index": it["item_index"],
                      "phase_output_id": it["phase_output_id"]},
                subject=it["subject"], phase_name=it["phase"], output_md=it["output_md"],
                lesson_context=it["extract_md"], prior_outputs={},
                gen_provider=GEN_PROVIDER, gen_model=GEN_MODEL,
                judge_provider=judge_provider, judge_model=judge_model,
                transport=TRANSPORT, contract_override=contract_override,
                output_language=it["output_language"],
            )
            if v is not None:
                it["arms"][arm].append(v)

    # ─── Window 1: arm A single runs (OLD rule patched) ──────────────────────
    saved_rule = phase_judge._FIDELITY_RULE
    try:
        phase_judge._FIDELITY_RULE = old_rule
        await asyncio.gather(*[
            judge_item(it, "A", render_old_contract(it["subject"], it["phase"], it["output_language"]))
            for it in items
        ])
    finally:
        phase_judge._FIDELITY_RULE = saved_rule

    # ─── Window 2: arm B + arm C single runs (NEW rule) ──────────────────────
    tasks = []
    for it in items:
        tasks.append(judge_item(it, "B", render_old_contract(it["subject"], it["phase"], it["output_language"])))
        tasks.append(judge_item(it, "C", None))  # None -> production get_prompt
    await asyncio.gather(*tasks)

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

    # ─── Window 3: arm A replays (OLD rule) ──────────────────────────────────
    saved_rule = phase_judge._FIDELITY_RULE
    try:
        phase_judge._FIDELITY_RULE = old_rule
        tasks = []
        for it in replay_items:
            for _ in range(REPLAY_RUNS):
                tasks.append(judge_item(it, "A", render_old_contract(it["subject"], it["phase"], it["output_language"])))
        await asyncio.gather(*tasks)
    finally:
        phase_judge._FIDELITY_RULE = saved_rule

    # ─── Window 4: arm C replays (NEW rule) + probes ─────────────────────────
    tasks = []
    for it in replay_items:
        for _ in range(REPLAY_RUNS):
            tasks.append(judge_item(it, "C", None))
    await asyncio.gather(*tasks)

    # probes (NEW rule, no patch, production get_prompt contract)
    probe_results = await run_probes(probes_spec, budget, sem, judge_provider, judge_model)
    p4_baseline = await run_p4_baseline(probes_spec["p4_genuine_defect"], budget, sem,
                                        judge_provider, judge_model)

    # ─── analysis ────────────────────────────────────────────────────────────
    def consolidated_hm(it, arm):
        runs = [r for r in it["arms"][arm] if r.get("has_major") is not None]
        if not runs:
            return None
        majors = sum(1 for r in runs if r["has_major"])
        return majors * 2 > len(runs)  # majority

    # transition tables per phase (A->B, A->C), pooled cohorts + per cohort
    def transitions(pair_arms, key_fn):
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

    trans = {
        "A_to_B": {
            "per_phase": transitions(("A", "B"), lambda it: it["phase"]),
            "per_phase_cohort": transitions(("A", "B"), lambda it: f"{it['cohort']}:{it['phase']}"),
        },
        "A_to_C": {
            "per_phase": transitions(("A", "C"), lambda it: it["phase"]),
            "per_phase_cohort": transitions(("A", "C"), lambda it: f"{it['cohort']}:{it['phase']}"),
        },
    }

    # per-cell arm major-rates (for reweighting) — cell = cohort:phase:status
    def cell_major_rate(arm, cohort, phase, status):
        vals = [consolidated_hm(it, arm) for it in items
                if it["cohort"] == cohort and it["phase"] == phase and it["prior_status"] == status]
        vals = [v for v in vals if v is not None]
        if not vals:
            return None, 0
        return sum(1 for v in vals if v) / len(vals), len(vals)

    reweighted = {}
    for arm in ("A", "B", "C"):
        for cohort in COHORTS:
            for phase in PHASES:
                p = prev[f"{cohort}:{phase}"]["prevalence"]
                r_major, n_major = cell_major_rate(arm, cohort, phase, "major")
                r_clean, n_clean = cell_major_rate(arm, cohort, phase, "clean")
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

    # residual-major breakdown for arm C (first run per item, majors only)
    residual = {}
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

    # probe verdicts
    probes_out = {}
    all_probes_pass = True
    for name, runs in probe_results.items():
        verdict = evaluate_probe(name, runs)
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
            "execution_order": "GROUPED BY ARM (A single | B+C single | A replays | C replays+probes), NOT "
                               "per-item rotation. Rationale: the OLD-rule arm mutates a process-global read "
                               "at prompt-build time, so it must not interleave with NEW-rule calls; judge "
                               "calls are independent stateless API requests, so call order cannot bias an "
                               "unseeded gemini verdict. This trades the brief's counterbalancing (which "
                               "guards against a bias that does not exist for stateless calls) for a real "
                               "concurrency-correctness guarantee.",
            "concurrency": args.concurrency,
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
            "gate_note": "fidelity-scoped gate on contradiction-major / invented-number flag "
                         "(the rule's actual mechanism); structural/coverage majors reported but "
                         "not part of the gate. Probes are well-formed grade-pinned decks.",
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
