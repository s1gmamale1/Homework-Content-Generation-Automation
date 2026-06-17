# Generate all 7 Gamified Practices every job — Implementation Plan

> **For agentic workers:** TDD per task, commit per task. Controller re-runs tests after every commit.

**Goal:** `flow_for` generates all four interactive mini-games every job (flow 8→11 content phases), so all 7 Gamified Practices are produced, none skipped. Backend-only.

**Spec:** `docs/superpowers/specs/2026-06-17-all-gamified-games-design.md`

**Commands:** backend `NOTION_API_KEY=dummy uv run python -m pytest tests/ -q`; single file `… tests/services/test_general_flow.py -q`.

---

### Task 1: `flow_for` emits all four mini-games (TDD)

**Files:**
- Modify: `tests/services/test_general_flow.py` (the 8-phase shape test)
- Modify: `app/services/flows.py:19-35`

- [ ] **Step 1 — rewrite the failing test.** Replace `test_flow_is_8_phases_with_subject_game` in `tests/services/test_general_flow.py` with:

```python
def test_flow_generates_all_seven_gamified_games():
    # Every job generates the FULL Gamified Practices set (7), skipping none:
    # rlc + error-detection + all four interactive mini-games + boss-arena.
    # Which game "fits" a subject is curated downstream, not by skipping.
    base = ["case-based-preview", "flashcards", "memory-check",
            "practice-rlc", "practice-error-detection"]
    games = ["practice-memory-match", "practice-tictactoe",
             "practice-jigsaw", "practice-sentence"]
    tail = ["boss-arena", "reflection"]
    all_gamified = {"practice-rlc", "practice-error-detection",
                    "practice-memory-match", "practice-tictactoe",
                    "practice-jigsaw", "practice-sentence", "boss-arena"}
    for subject in flows.SUPPORTED_SUBJECTS:
        seq = flows.flow_for(subject)
        assert len(seq) == 11
        assert seq[:5] == base
        assert seq[5:9] == games        # all four mini-games, deterministic order
        assert seq[9:] == tail
        assert all_gamified <= set(seq)  # all 7 gamified present, none skipped
        assert len(seq) == len(set(seq)) # no duplicate phases
```

(Leave `test_every_subject_game_is_registered_and_has_prompt` and the other tests in the file unchanged — `SUBJECT_GAME` still exists and each value still has a prompt.)

- [ ] **Step 2 — run, expect FAIL.** Run: `NOTION_API_KEY=dummy uv run python -m pytest tests/services/test_general_flow.py::test_flow_generates_all_seven_gamified_games -q` → FAIL (`len(seq) == 8`, not 11).

- [ ] **Step 3 — implement.** In `app/services/flows.py`, replace the `SUBJECT_GAME` comment + `_BASE_PHASES`/`flow_for` block with:

```python
# Per-subject RECOMMENDED game (metadata only) — the one mini-game that best
# fits the subject's content type. **Not consumed by the flow:** since every job
# now generates ALL four mini-games (see _GAMES / flow_for), SUBJECT_GAME no
# longer gates generation; it survives only as the "which game fits" hint for
# downstream curation (and one test). Do NOT delete it as "unused".
SUBJECT_GAME: dict[str, str] = {c: d.game for c, d in subjects.REGISTRY.items()}

_BASE_PHASES: list[str] = [
    "case-based-preview", "flashcards", "memory-check",
    "practice-rlc", "practice-error-detection",
]

# All four interactive mini-games run on EVERY job — the full Gamified Practices
# set (rlc + error-detection + these four + boss-arena = all 7) is generated,
# never skipped. Order is fixed here so phase_order / display order is
# deterministic. All four have PHASE_DEPS entries, so the wave scheduler runs
# them concurrently once their shared deps are met (minimal extra wall-clock).
_GAMES: list[str] = [
    "practice-memory-match", "practice-tictactoe",
    "practice-jigsaw", "practice-sentence",
]


def flow_for(subject: str) -> list[str]:
    if subject not in SUBJECTS:
        raise KeyError(f"Unsupported subject: {subject}")
    return [*_BASE_PHASES, *_GAMES, "boss-arena", "reflection"]
```

- [ ] **Step 4 — run, expect PASS.** Run: `NOTION_API_KEY=dummy uv run python -m pytest tests/services/test_general_flow.py tests/services/test_learning_flow.py tests/services/test_prompt_coverage.py -q` → PASS (prompt-coverage now also exercises all 4 games × every subject).

- [ ] **Step 5 — commit.**
```bash
git add app/services/flows.py tests/services/test_general_flow.py
git commit -m "feat(flows): generate all 7 gamified games every job (8->11 phases)"
```

### Task 2: Full suite + acceptance (real CLI smoke)

- [ ] **Step 1 — full suite.** Run: `NOTION_API_KEY=dummy uv run python -m pytest tests/ -q` → green (real-DB tests skip without `RUN_DB_INTEGRATION=1`).
- [ ] **Step 2 — real CLI smoke of a NEWLY-ADDED game.** In-process (no server): render + generate `practice-jigsaw` for `biology` (whose recommended game was `memory-match`, so jigsaw was previously NOT generated) via a real `claude` call. Confirm: `flow_for("biology")` contains `practice-jigsaw`, `get_prompt("biology","practice-jigsaw")` has no leftover `{{…}}`, and the model returns coherent non-empty markdown. Use a throwaway script (delete after), `DATABASE_URL` set to a dummy so `Settings` loads (usage write is best-effort). Record the char count + first ~400 chars as proof.

### Task 3: Finish

- [ ] De-stale live docs that state the phase count: grep `docs/HOW_IT_WORKS.md` / `docs/CODE_MAP.md` / `README.md` for "8-phase" / "8 phases" / the flow list and update to 11 (full Gamified set). 
- [ ] Worklog entry in `docs/memory/MASTER_MEMORY.md` (next free ID) + row in `docs/memory/INDEX.md`.
- [ ] `git mv` spec + plan into `docs/superpowers/specs/shipped/` and `plans/shipped/`.
- [ ] Push `feat/all-games`; open PR to `Nggaev-v2`.

## Self-review

- **Spec coverage:** flow change ✔ (Task 1), all-7-present assertion ✔ (Task 1 test), acceptance smoke ✔ (Task 2), docs/worklog/ship ✔ (Task 3). FE/pipeline/PHASE_DEPS/prompts/DB = "no change" per spec → no task, correct.
- **Type/name consistency:** `_GAMES` list[str]; `flow_for` returns list[str]; `SUBJECT_GAME` retained (name unchanged → `test_every_subject_game_is_registered_and_has_prompt` still resolves). Phase names match existing `prompts/_general/*.md` stems and `PHASE_DEPS` keys exactly.
- **No placeholders:** all code shown in full.
