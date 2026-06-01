# Project Roadmap — worked-up items

> Items promoted from [WISHLIST.md](./WISHLIST.md) once understood. Each entry states:
> **Issue** (what's wrong / wanted) · **Root cause** (why, with code/doc references) ·
> **Deliverable** (the concrete result after the fix). Move to "Shipped" when done.
> Local-only (gitignored `docs/memory/`).

---

## R1 — Inert subject prompts will mislead/break if the override layer is revived

- **Issue:** the per-subject `prompts/<subject>/*` dirs are dead but on disk (the `USE_SUBJECT_PROMPTS=False` override layer). If that switch is ever flipped True, two stale artifacts surface: (a) English prompts reference the deleted `classify.md` for CEFR; (b) `practice-rlc.md` files still carry a "reverse-test variant" the RLC spec never defined.
- **Root cause:** Path A (worklog 0019) intentionally left subject dirs untouched as a future override layer; `classify` was removed from the live path but not from those inert files; the spec-unsupported RLC reverse-test was only stripped from `_general/practice-rlc.md`, not the inert subject copies.
  - Refs: `app/services/prompts.py` `_resolve_dir`/`USE_SUBJECT_PROMPTS`; `prompts/english/classify.md`; `prompts/<subject>/practice-rlc.md`.
- **Deliverable:** when (if) the override layer is revived, scrub the inert subject prompts — remove `classify.md` CEFR references and the reverse-test variant — before flipping the switch.

---

## Shipped

_(none yet — move completed R-items here with their commit/worklog ref)_
