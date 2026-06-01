# Project Wishlist — capture inbox

> Raw capture of bugs / issues / ideas — **just the idea itself**, one line each.
> No analysis here. When an item is understood (root cause + deliverable), promote it
> to [ROADMAP.md](./ROADMAP.md) and remove it from this list.
> Local-only (gitignored `docs/memory/`).

## Open

- Scanned / image-only PDFs: TOC extraction unsupported (only text PDFs decode; image pages rely on gemini native read).
- Boss Arena `hints` allows 0 / unstructured — would like a 3-tier hint ladder (Why → How → synthesis).
- `mistake_provenance` tag (`source` | `inferred`) on the CBP common-mistake — deferred from WS1.
- Source-fidelity is detect-only (logs a warning on invented concept_ids) — could escalate to hard-fail / retry.
- `opencode` provider is implemented but never run against a real install — first action when installed: one real generation (watch for the stdin/positional hang). Detail: [[MASTER_MEMORY]] §0010.
- Bad book data: math-algebra book `9e7833bc…` has a 4 KB stub PDF (not a real textbook) — extraction would fail; clean up or replace.
- Confirm the English grade→CEFR ladder against the official Uzbek curriculum (values already adjusted this session — just needs curriculum-owner sign-off).

## Done / promoted

- W1 — `opencode` as 5th CLI provider — ✅ DONE 2026-05-29 (commit 8a96435), see [[MASTER_MEMORY]] §0010. (Verification follow-up moved to Open above.)
