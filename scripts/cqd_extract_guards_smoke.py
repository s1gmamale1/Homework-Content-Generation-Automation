"""Acceptance smoke for cq-d-extract-guards — real gemini-api calls (both guards).

This is the CLAUDE.md acceptance gate for the two extract guards:
  - Item 2: alphabet-plausibility gate on the raw local PDF text (Gate A) +
    vision recovery of a garbled (cp1251 mojibake) Cyrillic book.
  - Item 1: deterministic fidelity-candidate scan + flash `verify` discrimination
    (a drifted value is flagged; a faithful summary is clean).

Three checks:
  CHECK 1 — Item 2 detection (NO model call, always runs).
  CHECK 2 — Item 2 vision recovery (REAL gemini-api call over Vertex).
  CHECK 3 — Item 1 verify discrimination (REAL gemini-api call over Vertex).

CHECK 1 always runs. CHECKS 2 & 3 make paid api calls (require the Vertex SA env
GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT); if creds are missing the
call raises — we catch per-check, print FAIL (api error), and exit 1 (never
swallowed).

Run: cd /Users/macmini5/Documents/HCGA-cqd && uv run python -m scripts.cqd_extract_guards_smoke
"""
import asyncio
import glob
import sys
from pathlib import Path

from app.config import settings  # noqa: F401 — import triggers load_dotenv(.env)
from app.services import agent

# The RU cp1251-mojibake book (garbled text layer) and a clean Uzbek-Latin book.
_F20_PREFIX = "f20db30c"
_5E_PREFIX = "5e295cbc"


def _resolve_pdf(prefix: str) -> Path:
    """Resolve `var/books/<prefix>*/source.pdf` to a real path. Tries a few
    candidate roots so the smoke works from a worktree that has no local
    var/books (the host-specific book store lives in the main checkout)."""
    roots = [
        Path(settings.var_dir) / "books",
        Path.cwd() / "var" / "books",
        Path("/Users/macmini5/Documents/Homework-Content-Generation-Automation/var/books"),
    ]
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        hits = sorted(glob.glob(str(root / f"{prefix}*" / "source.pdf")))
        if hits:
            return Path(hits[0])
    raise FileNotFoundError(
        f"no source.pdf for book prefix {prefix!r} under any of: "
        + ", ".join(str(r) for r in roots)
    )


# --------------------------------------------------------------------------- #
# CHECK 1 — Item 2 detection (no model)
# --------------------------------------------------------------------------- #
def check1_detection() -> bool:
    print("=" * 72)
    print("CHECK 1 — Item 2 plausibility detection (no model call)")
    print("=" * 72)
    ok = True

    # (a) garbled RU cp1251 book → must fail with a "plausibility" reason.
    try:
        garbled_pdf = _resolve_pdf(_F20_PREFIX)
        garbled_text = agent.read_whole_book_text(garbled_pdf)
        ratio = agent._alpha_plausibility_ratio(garbled_text)
        reason = agent.validate_extract_text(garbled_text)
        print(f"  garbled book: {garbled_pdf}")
        print(f"    alpha_plausibility_ratio = {ratio:.3f}")
        print(f"    validate_extract_text -> {reason!r}")
        if reason is None:
            print("  FAIL: garbled book accepted (expected a failure reason)")
            ok = False
        elif "plausib" not in reason.lower():
            print("  FAIL: failure reason does not mention plausibility")
            ok = False
        else:
            print("  PASS: garbled book rejected with a plausibility reason")
    except Exception as exc:
        print(f"  FAIL (error): {exc!r}")
        ok = False

    # (b) clean Uzbek-Latin book → must be accepted (None).
    try:
        clean_pdf = _resolve_pdf(_5E_PREFIX)
        clean_text = agent.read_whole_book_text(clean_pdf)
        ratio = agent._alpha_plausibility_ratio(clean_text)
        reason = agent.validate_extract_text(clean_text)
        print(f"  clean book: {clean_pdf}")
        print(f"    alpha_plausibility_ratio = {ratio:.3f}")
        print(f"    validate_extract_text -> {reason!r}")
        if reason is None:
            print("  PASS: clean book accepted (None)")
        else:
            print(f"  FAIL: clean book rejected: {reason!r}")
            ok = False
    except Exception as exc:
        print(f"  FAIL (error): {exc!r}")
        ok = False

    print(f"CHECK 1: {'PASS' if ok else 'FAIL'}")
    return ok


# --------------------------------------------------------------------------- #
# CHECK 2 — Item 2 vision recovery (real gemini-api call)
# --------------------------------------------------------------------------- #
async def check2_vision_recovery() -> bool:
    print("=" * 72)
    print("CHECK 2 — Item 2 vision recovery (REAL gemini-api call)")
    print("=" * 72)
    try:
        garbled_pdf = _resolve_pdf(_F20_PREFIX)
        summary, ptok, otok = await agent.summarize_lesson_vision(
            provider="gemini",
            model="gemini-3.5-flash",
            pdf_path=garbled_pdf,
            section_title="Algebra",
            section_number="1",
            page_start=20,
            page_end=24,
            homework_job_id=None,
            phase_output_id=None,
            transport="api",
        )
    except Exception as exc:
        print(f"CHECK 2: FAIL (api error): {exc!r}")
        return False

    ratio = agent._alpha_plausibility_ratio(summary)
    print(f"  prompt_tokens={ptok} output_tokens={otok}")
    print(f"  summary[:200]={summary[:200]!r}")
    print(f"  alpha_plausibility_ratio(summary) = {ratio:.3f}")
    ok = True
    if len(summary) <= 200:
        print(f"  FAIL: summary too short (len={len(summary)}, need > 200)")
        ok = False
    if ratio < 0.9:
        print(f"  FAIL: summary ratio {ratio:.3f} < 0.9 (Cyrillic not recovered / still mojibake)")
        ok = False
    if ok:
        print("  PASS: vision recovered readable Cyrillic (non-trivial, ratio >= 0.9)")
    print(f"CHECK 2: {'PASS' if ok else 'FAIL'}")
    return ok


# --------------------------------------------------------------------------- #
# CHECK 3 — Item 1 verify discrimination (real gemini-api call)
# --------------------------------------------------------------------------- #
async def check3_verify_discrimination() -> bool:
    print("=" * 72)
    print("CHECK 3 — Item 1 verify discrimination (REAL gemini-api call)")
    print("=" * 72)
    ok = True
    try:
        clean_pdf = _resolve_pdf(_5E_PREFIX)
        book_text = agent.read_whole_book_text(clean_pdf)
    except Exception as exc:
        print(f"CHECK 3: FAIL (error reading book): {exc!r}")
        return False

    # --- (a) drifted summary: an ungrounded value the flash verify should catch.
    drifted_summary = (
        "Ushbu darsda kasrlar va algebraik ifodalar bilan ishlash koʻrib "
        "chiqiladi. Ishlangan misol natija −7/(3b) boʻladi."
    )
    drift_cands = agent.extract_fidelity_candidates(drifted_summary, book_text)
    print(f"  drift candidates = {drift_cands!r}")
    if not drift_cands:
        print("  FAIL: expected a non-empty candidate list for the drifted summary")
        ok = False
    else:
        try:
            mismatches = await agent.verify_extract_fidelity(
                summary=drifted_summary,
                book_text=book_text[:8000],
                candidates=drift_cands,
                provider="gemini",
                model="gemini-3.5-flash",
                transport="api",
                homework_job_id=None,
                phase_output_id=None,
            )
        except Exception as exc:
            print(f"CHECK 3: FAIL (api error): {exc!r}")
            return False
        print(f"  drifted verify mismatches = {mismatches!r}")
        if mismatches:
            print("  PASS: verify flagged the ungrounded value as a mismatch")
        else:
            print("  FAIL: verify returned [] for a plainly ungrounded value")
            ok = False

    # --- (b) faithful summary: grounded text only → verify must be clean.
    faithful_summary = book_text[:600]
    faithful_cands = agent.extract_fidelity_candidates(faithful_summary, book_text)
    print(f"  faithful candidates = {faithful_cands!r}")
    if not faithful_cands:
        print("  NOTE: no candidates for the faithful summary — verify skipped (trivially clean)")
    else:
        try:
            faithful_mm = await agent.verify_extract_fidelity(
                summary=faithful_summary,
                book_text=book_text[:8000],
                candidates=faithful_cands,
                provider="gemini",
                model="gemini-3.5-flash",
                transport="api",
                homework_job_id=None,
                phase_output_id=None,
            )
        except Exception as exc:
            print(f"CHECK 3: FAIL (api error): {exc!r}")
            return False
        print(f"  faithful verify mismatches = {faithful_mm!r}")
        if faithful_mm:
            print(f"  FAIL: verify flagged a grounded (faithful) summary: {faithful_mm!r}")
            ok = False
        else:
            print("  PASS: verify returned [] for the faithful summary")

    print(f"CHECK 3: {'PASS' if ok else 'FAIL'}")
    return ok


async def main() -> None:
    results: dict[str, bool] = {}

    # CHECK 1 always runs (no model, no creds).
    results["CHECK 1 (detection)"] = check1_detection()
    # CHECKS 2 & 3 make real api calls.
    results["CHECK 2 (vision recovery)"] = await check2_vision_recovery()
    results["CHECK 3 (verify discrimination)"] = await check3_verify_discrimination()

    print("=" * 72)
    print("SUMMARY")
    for name, passed in results.items():
        print(f"  {name}: {'PASS' if passed else 'FAIL'}")
    print("=" * 72)
    if all(results.values()):
        print("SMOKE PASS: both extract guards proven over real gemini-api")
    else:
        print("SMOKE FAIL: see per-check output above")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
