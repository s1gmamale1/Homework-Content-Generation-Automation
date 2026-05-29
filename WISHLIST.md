# Wishlist / Bug Tracker

Bugs and issues found during testing, to be fixed later.
Move items to **Fixed** when resolved (with the date).

Status key: `[ ]` open · `[~]` in progress · `[x]` fixed

---

## Open

<!-- Example:
- [ ] 2026-05-29 — Job page warmup loader hangs on slow network — repro: throttle to 3G, submit a job. Priority: med
-->

- [ ] 2026-05-29 — app/schemas/platform/real_life_challenge.py is a HAND-MIRROR of the platform RealLifeChallengeCase, not the real schema. Must sync from s1gmamale1/Homeworks when available — divergence would let an invalid beta export pass our contract test. Priority: high
- [ ] 2026-05-29 — Migration 0010 not applied/tested against a live Postgres (no DB available here). Verify `alembic upgrade head` and downgrade work. Priority: med
- [ ] 2026-05-29 — PRE-EXISTING (not from RLC work, confirmed via git stash on clean base): test_pipeline_synth.py::TestSynthReading::test_checkpoint_count_shown fails — reading synth branch reads cp.after_paragraph but the test's checkpoint stub omits it (pipeline.py:103 AttributeError). Priority: med
- [ ] 2026-05-29 — PRE-EXISTING (not from RLC work): test_flows.py::TestStripSvgs::test_svg_replaced_with_placeholder fails on clean base. Priority: low
- [ ] 2026-05-29 — Integration tests (31, test_api_*) error at setup, not run: project pins NO pytest/asyncio config (no asyncio_mode, pytest not in uv.lock). Modern pytest 9 + pytest-asyncio strict mode rejects the async `tables` fixture. Need `asyncio_mode=auto` config + pinned test deps + a Postgres test DB. Priority: med

## Verification log

- [x] 2026-05-29 — RLC slice: full suite RAN in a throwaway venv (.venv-test, py3.14, pydantic 2.13). Result: 198 passed, 2 failed, 31 errors. Both failures are PRE-EXISTING (confirmed identical on clean base via git stash). All 31 errors are the integration-harness issue above (not RLC code). The 2 new RLC test files pass 11/11. py_compile clean on all 12 changed files. NOT yet verified: alembic migration vs live Postgres; integration tests vs a real DB.

## Fixed

<!-- Example:
- [x] 2026-05-29 — (fixed 2026-05-30) Back button on job page navigated to wrong route
-->
