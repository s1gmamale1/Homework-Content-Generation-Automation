# Generate all 7 Gamified Practices every job (Design)

**Date:** 2026-06-17
**Branch / worktree:** `feat/all-games` (`/Users/macmini5/Documents/HCGA-wt-all-games`)
**Status:** spec for review (gated pipeline — no code until approved).

## Problem

A job currently generates only **one** of the four interactive mini-games — `flow_for` inserts `SUBJECT_GAME[subject]` (the single subject-matched game) at position 5. So of the **7 Gamified Practices** in `docs/Infra_prompts/Gamified Practices/` (Boss Arena, Error Detection, Jigsaw Matching, Memory Matching, Real Life Challenge, Sentence Filling, TicTacToe), a job produces only: `practice-rlc` + `practice-error-detection` + `boss-arena` + **one** of {`practice-memory-match`, `practice-tictactoe`, `practice-jigsaw`, `practice-sentence`}. The other three mini-games are never generated.

## Goal (locked with user)

Generate **all 7 Gamified Practices on every job, skipping none.** Which game best "fits" a subject is curated **downstream/manually**, not by skipping generation — so generation must produce the full set.

## Decision

`flow_for(subject)` emits all four mini-games instead of the single `SUBJECT_GAME` pick:

```
extract  (head)
case-based-preview → flashcards → memory-check
practice-rlc → practice-error-detection
practice-memory-match → practice-tictactoe → practice-jigsaw → practice-sentence
boss-arena → reflection
```

Content phases per job: **8 → 11** (excluding the pinned head `extract`; full sequence 9 → 12). All 7 Gamified Practices (`rlc`, `error-detection`, 4 mini-games, `boss-arena`) are now present.

**`SUBJECT_GAME` is kept** (derived from `subjects.REGISTRY[*].game`) but **no longer gates the flow** — it becomes the per-subject *recommended-game* hint for the downstream "which game fits" curation. (Rejected removing it: it ripples into the registry `game` field + would lose the recommendation signal the user wants; keeping is zero-risk.) After this change `flow_for` no longer reads it, so it is **runtime-dead** (referenced only by the test + downstream curation) — the reworded code comment **must say "recommendation hint, not consumed by the flow"** so a future reader doesn't "clean up" a seemingly-unused map.

## Scope — backend only

Verified against the live code (`origin/Nggaev-v2`), nothing else needs to change:

- **`app/services/flows.py`** — the only functional change: add `_GAMES`, `flow_for` returns `[*_BASE_PHASES, *_GAMES, "boss-arena", "reflection"]`; reword the `SUBJECT_GAME` comment.
- **FE: no change.** `web/src/routes/preview.tsx` renders phases dynamically (`.sort((a,b)=>a.phase_order-b.phase_order)` → `phases.map(...)`), and `PHASE_LABELS` already has labels for all six practice games. The three added game sections render automatically.
- **Pipeline/scheduler: no change.** `pipeline.py` derives everything from `flow_for` (`sequence=["extract",*flow_for(subject)]`, `content_phases=sequence[len(head_phases):]`, `total_phases_hint` computed). No hardcoded phase count.
- **PHASE_DEPS: no change.** All four mini-games already have dependency entries → the wave scheduler launches them concurrently once their (shared) deps (`case-based-preview`/`flashcards`/`memory-check`) are met.
- **Prompts: no change.** All 7 gamified prompts exist in `prompts/_general/` and are already covered for every subject by `test_prompt_coverage`.
- **DB: no change.** `phase_outputs` rows keyed by `(job, phase_order)`; more phases = more rows, no schema impact.
- **`SUBJECT_GAME` consumers:** only `flows.py` + `tests/services/test_general_flow.py` (grep-verified) → only the test needs updating.

## Implications (accepted)

- **Cost/time: ≈+37% phase count, but noticeably *less* in $.** Phrase it that way in the campaign cost model — **not** "+37% dollars" (over-budgets). Why: `extract` (the expensive pinned whole-PDF gemini-flash read) is unchanged, and the 3 added phases are CBP-style games with **small structured outputs** — and output tokens bill ~5× input, so small outputs keep their $ down. Wall-clock impact is smaller still because the four mini-games run **in parallel** (shared deps, no inter-game deps).
- **Odd-fit combos** (e.g. `practice-sentence` for physics) will now be generated. Accepted per "skip none"; the family-aware prompt still produces a best-effort version, and curation drops poor fits downstream.

## Verification plan

- **Tests:** rewrite `test_general_flow.py::test_flow_is_8_phases_*` → assert 11 phases, base first 5, the 4 mini-games at 5:9, `boss-arena`+`reflection` last, all 7 gamified present, no duplicates. `test_every_subject_game_is_registered_and_has_prompt` stays valid. `test_prompt_coverage` / `test_learning_flow` auto-cover the new phases.
- **Full suite green** (`uv run python -m pytest tests/ -q`, `NOTION_API_KEY` set).
- **Acceptance gate (real CLI smoke):** in-process generate a **newly-added** game for a subject that didn't get it before — e.g. `practice-jigsaw` for `biology` (whose `SUBJECT_GAME` was `memory-match`) via a real `claude`/`gemini` call — confirm coherent markdown + zero leftover tokens. Proves the added games generate end-to-end, not just that the flow list grew.

## Risks

- Cost increase (above) — surfaced, user-accepted.
- A future per-job phase-count assumption elsewhere — grep-verified none today; the plan re-confirms before finishing.
