# Notion-Collision Repair — Correction (R26): mixed-page recovery, apply safety, group precedence

> **Follow-up to merged PR #122 (`10f92d4`, worklog 0165).** A NEW branch/PR off current `Nggaev-v2`; it edits the now-merged `scripts/repair_notion_collisions.py` forward — it does NOT modify #122's merged state. `--apply` stays FROZEN (ROADMAP R26) until this ships and is GK-gated. Author builds; does NOT self-merge.

> **SUB-SKILL:** implement via `superpowers:subagent-driven-development`, one fresh subagent per task, controller stress-tests every commit (read diff + re-run tests). Checkbox steps.

---

## Approach & key decisions

- **Three defects, all independently re-verified (2026-08-10) against the merged code + `edu_copy`.** (1) mixed-page contamination the DB-only repair can't fix; (2) unguarded/stale `--apply` (TOCTOU); (3) fallback precedence compares a proven archive against a generation-completion guess (test `:276` codifies the wrong result). Refs in ROADMAP R26.
- **Mixed-page recovery — authoritative rewrite + PRUNE (rewrite ALL retained owners, don't skip "clean").** `notion_archive._push_to_notion(replace=True, homework_page_id=…)` rewrites the *owner's* leaves but leaves **orphaned extra leaves** (a non-owner's `practice-*` child pages) in place — a replace-only refresh does NOT clean a mixed page. And phase-set classification alone is NOT ownership: if a newer non-owner `auto_replace`d a *shared* leaf, that leaf carries the wrong job's content under the *same* phase name — a `leaf-phase ⊆ owner-set` "clean" verdict would miss it. So per retained-owner page we **uniformly**: (a) **rewrite the owner's leaves authoritatively** — `_push_with_retry(replace=True, homework_page_id=owner.page_id)` with the **owner job's** `phase_outputs` (this makes the owner's content authoritative on every shared leaf, fixing same-phase-wrong-content **without** downloading bytes); (b) **enumerate** the page's actual leaves (`get_child_pages` on the homework page + the "Gamified Practices" container; each leaf's phase from its `{phase_name}.md` attachment via `get_block_children` → `file.name`) and, **only after the rewrite succeeds**, `delete_block` every leaf whose phase the owner did NOT produce. A failed rewrite prunes **nothing** (never delete with no clean owner content in place). We do NOT skip "clean" pages — the rewrite is idempotent and the churn (re-upload, new block ids) is acceptable for a one-time repair, and it's the only content-free way to close same-phase-wrong-content. Classification (`clean|mixed|unreadable` + extra-leaf list) is computed for the **report**, not to gate the rewrite. **Ordering is load-bearing:** the DB non-owner pointers are cleared FIRST (own transaction, committed), THEN the rewrite runs — otherwise the `notion_archive.py:415-419` footgun (forcing a page a non-owner still points at) destroys content.
  - **Explicitly out of scope (documented limitation):** content-hash provenance (download each `.md`, diff vs `phase_outputs.output_md`). Unnecessary because we rewrite the owner's content authoritatively rather than *verify* whose content is present. The `{phase_name}.md` attachment name identifies *which phases* a page hosts, which is all pruning needs.
  - **Docstring correction (required):** remove the merged claims *"Nothing in Notion was overwritten"* / *"deliberately NO --force / Notion-writing path"*. New premise: the rewrite is **prohibited before** the DB clear, **required after** for every retained owner.
- **Refresh staging via a persisted MANIFEST (fixes: a standalone refresh finds no collisions).** After `--apply` NULLs the non-owner pointers, `load_colliding_sections`'s `HAVING count(*)>1` returns nothing — so the Notion refresh can NOT re-derive its plan from the DB. `--apply` therefore **emits a manifest** (`--manifest-out <path>`): the executed plan as the **full expected snapshot** (see hash below) plus each owner's `page_id`, `section_id`, and `job_id`. `--refresh-notion --plan-file <manifest>` consumes it: re-verifies the manifest hash, re-checks each owner pointer is still intact (owner section still points at its page — a light expected-state check, since the collision is gone), then runs the rewrite+prune. **Idempotent + re-runnable** — a partial Notion failure is recovered by re-running `--refresh-notion` from the same manifest. `--apply` and `--refresh-notion --plan-file` are the two operator gestures; a bare `--refresh-notion` without a manifest is rejected.
- **Apply safety (TOCTOU) — plan-hash over the FULL expected snapshot + re-read + expected-state predicates.** The `plan-hash` covers **every field the guarded updates depend on**, not just ids: per non-owner section its expected `notion_homework_page_id` + `notion_archived_job_id`; per job-to-unstamp its expected `notion_archived_at`; per owner its `section_id` + `page_id` + `owner_source`. (A timestamp/pointer change must move the hash — the earlier ids-only hash would not have.) Dry-run prints it; `--apply` **requires** `--expect-plan-hash=<hash>` and, *inside the write transaction*, **re-reads** the colliding sections, **rebuilds** the plan, recomputes the hash, and **aborts** if it differs. The `apply_plan` UPDATEs switch from unconditional `WHERE id = ANY(:ids)` to a **VALUES-joined** update carrying each row's *expected* `notion_homework_page_id` / `notion_archived_at` in the `WHERE`; assert `result.rowcount == len(expected)` and abort on any mismatch. Operational note in `--apply` banner + runbook: **drain all archivers first** (no shared DB lock structurally excludes the race).
- **CLI injectability for the acceptance test.** `run()` builds the real `NotionClientWrapper` today, so a refresh-helper-only seam can't exercise the actual CLI path. Add a `client_factory: Callable[[], NotionClientWrapper] = _default_client` parameter to `run()` (and thread the session/engine already-injectable path); the acceptance test drives the real `run(..., client_factory=fake)` end-to-end, asserting zero real network calls.
- **Group-level precedence — proven-archive tier before completion guesses.** Rewrite the per-section `effective_push` comparison in `plan_group` into group evidence tiers: **(1)** earliest real `notion_archived_at` across *every* member (rungs 1+3 — proven push); **(2)** only if NO member has any archive timestamp, earliest stamped-job `completed_at`; **(3)** then earliest row-level done `completed_at`; **(4)** else `unresolvable`. A section that never archived can never own a page over one that did. **Fix the mis-asserting test at `:276`** to expect the archived section (S1) as owner. Note: does not change today's 18 owner candidates (all have real archive timestamps) — confirm that at execution against the live snapshot.
- **Safety during development — ZERO real Notion writes, ZERO production `--apply`.** Every test injects a fake `NotionClientWrapper` (the client is already injectable via `_push_to_notion`'s `find_or_create` param and by passing a stub client) and uses a scratch DB (`edu_scratch_notionrepair`, superuser URL). Acceptance is a mocked-Notion + scratch-DB integration test, never the real API and never production rows.
- **Verified load-bearing facts (agent code-map, 2026-08-10):** `_push_to_notion`/`_push_with_retry` `replace=True` + `clear_content_blocks` is the rewrite primitive; `client.delete_block` archives one child-page; `get_child_pages`/`get_block_children` read leaves + `file.name`; the merged apply seam is a single `engine.begin()` in `run()` (`:476-477`); precedence lives in `plan_group._earliest` (`:229-230`); the footgun is `notion_archive.py:415-419`.

## Global Constraints
- New branch off `origin/Nggaev-v2` HEAD; **stage only the files each task lists** (other sessions commit to this branch's neighbourhood). Never `git add -A`.
- No schema change, no migration. No production writes of any kind during dev.
- `--apply` remains gated behind operator action; this PR does not run it.
- Worklog number: **re-check the INDEX tail at finish** (0166 is highest today → likely 0167, but numbers go stale mid-lane).

### Gate-mandated invariants (must stay explicit — Tasks 3 & 5)
1. **Refresh validates the manifest INTERNALLY, not against the post-apply DB.** On `--refresh-notion --plan-file`, verify the manifest's own stored hash matches a recompute over the manifest's contents. Do **NOT** re-derive the pre-apply expected snapshot and compare it against the current DB — after `--apply` the DB legitimately changed (non-owner pointers NULLed), so that comparison would always fail. Instead, per owner, re-read and confirm the **owner's** `notion_homework_page_id` and `notion_archived_job_id` are still the manifest's values (owners were never cleared) before rewriting; skip + report any owner whose live pointer drifted.
2. **Reject invalid flag combinations with a clear non-zero exit + message:** bare `--refresh-notion` (no `--plan-file`); `--refresh-notion` with an unreadable/hash-mismatched manifest; `--apply` without `--expect-plan-hash`; `--apply` without `--manifest-out`. Test each rejection.

---

## Task 1 — Group-level precedence tiers + fix the mis-asserting test

- [ ] **RED:** rewrite `tests/scripts/test_repair_notion_collisions.py::test_group_with_no_stamped_job_still_resolves_an_owner` (`:276`) to assert the **archived** section (S1, `notion_archived_at=+5h`) owns the page over S2 (`completed_at=+1h`, never archived); add a sibling test: a group where NO member ever archived falls to earliest completion. Run — MUST fail against current code.
- [ ] **GREEN:** in `plan_group` (`repair_notion_collisions.py:225-246`) replace the two `_earliest(effective_push)` calls with tiered group selection: tier-1 = min `notion_archived_at` across all members' jobs (stamped or row); tier-2 = min stamped `completed_at`; tier-3 = min row done `completed_at`; else `owner=None` (`unresolvable`). Keep `naive_completed`/`ordering_disagreement` for reporting. Preserve `owner_source` labels.
- [ ] **VERIFY:** file green; full suite green.
- [ ] **COMMIT:** `scripts/repair_notion_collisions.py tests/scripts/test_repair_notion_collisions.py`.

## Task 2 — Full-snapshot plan-hash + manifest (dry-run prints hash; --apply emits manifest)

- [ ] **RED:** `test_plan_hash_covers_expected_state` — two plans with identical ids but a **changed expected `notion_archived_at` or `notion_homework_page_id`** produce **different** hashes (proves state, not just ids, is covered); order-independence holds. `test_manifest_roundtrips` — `manifest_from_plans(plans)` → JSON → `manifest_load()` reproduces the same owner set + hash. MUST fail.
- [ ] **GREEN:** add `plan_hash(plans) -> str` over the full expected snapshot (per-section expected `page_id`+`archived_job_id`, per-job expected `archived_at`, owner `section_id`+`page_id`+`owner_source`, all sorted). Add `manifest_from_plans`/`manifest_load` (JSON: version, hash, owners `[{page_id, section_id, job_id, phase_set}]`, expected-snapshot rows). Print the hash in the dry-run footer. Add `--expect-plan-hash`, `--manifest-out`, `--plan-file` to `_parse_args`.
- [ ] **VERIFY + COMMIT:** `scripts/repair_notion_collisions.py tests/scripts/test_repair_notion_collisions.py`.

## Task 3 — Expected-state predicates + re-read guard in apply

- [ ] **RED (real-DB, scratch):** `test_apply_aborts_on_stale_plan` — seed a collision, build plan, mutate a section's `notion_homework_page_id` out from under it, run apply → MUST raise/abort with zero writes (rowcount mismatch). `test_apply_rejects_wrong_expect_hash`. MUST fail.
- [ ] **GREEN:** in `run()`, on `--apply`: require `--expect-plan-hash`; inside `engine.begin()` re-read sections, rebuild plan, recompute hash, abort if ≠ expected. Rewrite `apply_plan` (`:426-451`) to VALUES-joined UPDATEs carrying expected `page_id`/`notion_archived_at` per row; assert `rowcount == len(expected)` else raise (transaction rolls back). Add the drain-archivers line to `APPLY_BANNER`.
- [ ] **VERIFY + COMMIT:** `scripts/repair_notion_collisions.py tests/scripts/test_repair_notion_collisions.py`.

## Task 4 — Notion leaf enumeration + classification (read-only, report-only)

- [ ] **RED:** new `tests/scripts/test_repair_notion_refresh.py` with a fake client — `classify_page(client, page_id, owner_phase_set)` returns `clean` (leaf phases ⊆ owner), `mixed` (extras present, listing the extra phases + their child-page ids to prune), `unreadable` (client raises). MUST fail.
- [ ] **GREEN:** add `classify_page(...)` — walk homework page → `get_child_pages`; for the "Gamified Practices" container, walk its children; resolve each leaf's phase via its `{phase_name}.md` attachment (`get_block_children` → first `file` block `file.name`), fall back to leaf title via `PHASE_TITLES`. Return structured result incl. extra child-page ids. Read-only. **Classification is report/prune-input only — it never decides whether to rewrite** (Task 5 rewrites every owner).
- [ ] **VERIFY + COMMIT:** `scripts/repair_notion_collisions.py tests/scripts/test_repair_notion_refresh.py`.

## Task 5 — Authoritative rewrite + prune (all owners), consumed from the manifest

- [ ] **RED:** `test_refresh_rewrites_every_owner_and_prunes_after_success` (fake client): owner leaves rewritten (`replace=True`) for a `clean` page too (no skip); extra child pages `delete_block`'d **only after** a successful rewrite. `test_failed_rewrite_prunes_nothing` — rewrite raises → **zero** `delete_block` calls for that page, recorded as failed, other pages proceed. `test_refresh_requires_manifest` — `--refresh-notion` without `--plan-file` exits non-zero. `test_refresh_ordering` — every owner pointer is intact (DB clear already committed) before any Notion call. MUST fail.
- [ ] **GREEN:** add `refresh_owner_pages(client, manifest, load_owner_phase_md) -> RefreshReport`; consumed by `--refresh-notion --plan-file <manifest>` (verify hash + owner-pointer-intact, then run). Per owner (ALL of them): `_push_with_retry(replace=True, homework_page_id=owner.page_id, phase_md=<owner job's done non-extract outputs>)`; on success, `classify_page` + `delete_block` each extra; on rewrite failure, record + prune nothing. Also emit the manifest at `--apply` time (`manifest_from_plans` → `--manifest-out`). Import `notion_archive`/`NotionClientWrapper`; obtain the client via `run()`'s `client_factory`. Fail-open per page; aggregate into the report.
- [ ] **VERIFY + COMMIT:** `scripts/repair_notion_collisions.py tests/scripts/test_repair_notion_refresh.py`.

## Task 6 — Docstring correction + report surfaces mixed/clean/unresolved

- [ ] Correct the module docstring (`:1-52`): remove "nothing overwritten / no Notion path"; state prohibited-before / required-after for the rewrite; document the two-gesture flow — `--apply --expect-plan-hash --manifest-out` then `--refresh-notion --plan-file` — and drain-archivers.
- [ ] Extend `format_summary`/`format_group` to report per-page `clean|mixed|unresolved|unreadable` + pruned-leaf counts + refresh outcomes. Test the formatter (pure).
- [ ] **VERIFY + COMMIT.**

## Task 7 — Acceptance: real `run()` CLI, mocked Notion + scratch DB — NO real API, NO production apply

- [ ] End-to-end scratch-DB test driving the **actual `run()` CLI path** with `client_factory=fake` (proves the whole flow is injectable, not just the helper):
  - Seed a mixed collision (owner 8 phases + non-owner 3 extra). Dry-run → capture printed `plan-hash`.
  - `run(apply=True, expect_plan_hash=<captured>, manifest_out=<path>, client_factory=fake)` → non-owner pointers NULLed via VALUES-joined expected-state guards; manifest written.
  - `run(refresh_notion=True, plan_file=<manifest>, client_factory=fake)` → owner page rewritten, 3 extras pruned; **assert zero real network calls**.
  - Negative legs: `--apply` with a wrong `--expect-plan-hash` → aborts, zero writes; a mutated-out-from-under-it section → rowcount-mismatch abort; a fake whose rewrite raises → that page's extras NOT pruned, others fine; `--refresh-notion` without `--plan-file` → non-zero exit.
- [ ] Report: DB rows changed, Notion calls the fake recorded, `$0` (no model, no real API, no production rows).
- [ ] **COMMIT** the acceptance test.

## Task 8 — Finish

- [ ] `git fetch origin` → if `Nggaev-v2` moved, rebase, re-run suite. (Expect movement — active branch.)
- [ ] De-stale `docs/HOW_IT_WORKS.md` / `docs/CODE_MAP.md` where they describe the repair script.
- [ ] Worklog (next free number — **re-check INDEX tail**) in `MASTER_MEMORY.md` + `INDEX.md` row.
- [ ] Close R26 in `docs/memory/ROADMAP.md` (move to Shipped ledger); keep the `--apply` FROZEN-until-run note as an operator action.
- [ ] `git mv` this plan → `docs/superpowers/plans/shipped/`.
- [ ] `superpowers:finishing-a-development-branch` — push + open PR. **Do NOT self-merge** (gatekeeper role) — GK/user gates.

## Out of scope (deliberate)
- Running `--apply` / `--refresh-notion` against production (separate operator authorization, after gate).
- Content-hash fingerprinting (filename/child-page identity suffices).
- Any change to `notion_archive.archive_job`'s normal flow, or to merged #122's history.
