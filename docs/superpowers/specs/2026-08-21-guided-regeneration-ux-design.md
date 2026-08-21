# Guided Regeneration UX and Campaign Version Design

**Date:** 2026-08-21  
**Status:** User-approved visual design; implementation not started  
**Base:** `test/regen-v3-local@ace86c0a`  
**Approved mockup direction:** Guided flow A + full-rebuild-first A1

## Purpose

Make versioned homework regeneration safe and understandable for a non-technical operator.
The current eight-section wizard exposes dependency mechanics, role models and transport details
before the operator can answer the ordinary question: “Which lessons should use the new prompts?”
It also loses the unfinished selection after a refresh or browser restart.

The replacement is a four-step guided flow that:

1. remembers an unfinished draft on the same browser;
2. makes a complete, consistent rebuild the default;
3. keeps selective phase regeneration available without leading with it;
4. assigns one explicit publication version to the whole campaign;
5. verifies and shows the exact Notion Lesson Topic destination before model spend; and
6. combines campaign creation and canary launch into one operator action while retaining the
   existing canary review gate and automatic post-approval publication.

## Non-goals

- Changing the regeneration DAG, judge retry policy, snapshot model or publisher lease model.
- Replacing or deleting the original `Homework` page.
- Adding a second publication-approval gate.
- Editing prompts or restoring the prompt-set registry.
- Redesigning Fleet batch generation.
- Merging into `Nggaev-v2`, pushing a branch or changing an existing PR as part of this design.

## Locked operator decisions

- Use the guided four-step layout, not the dense one-screen workspace.
- Step 2 leads with **Rebuild the whole homework**.
- Selective regeneration remains available as a secondary mode.
- Fresh extraction remains off by default.
- Drafts survive refreshes and full browser restarts.
- A draft remains until campaign creation succeeds or the operator chooses **Discard draft**.
- Publication version is campaign-wide. The operator chooses `V3` once and every target in that
  campaign publishes as `Homework V3`, even when its source is V1.
- Canary review remains the only human content-approval gate.
- Approving the canary automatically publishes successful targets and releases the remaining
  bounded waves. There is no separate publication approval.
- A revision must not silently create a duplicate Lesson Topic page when the correct existing page
  can be identified.

## Four-step flow

### Step 1 — Lessons

The operator narrows by subject, grade and output language, then searches and checks lessons.
Textbook identity stays visible as secondary context so duplicate lesson titles remain
distinguishable, but choosing a textbook is no longer presented as a separate conceptual task.

The current `/books` request returns only its default first 100 rows, which hid the seeded Grade 9
Uzbek Chemistry book in a 246-book database. The new picker must page through the books endpoint
until exhaustion (or use an equivalent bounded server-side search) rather than assuming the first
page is the whole library. Eligible lessons remain fetched one selected book at a time.

Each lesson row shows:

- lesson number and title;
- subject, grade, textbook and output language;
- current published homework version;
- the proposed campaign version; and
- a cheap DB hint: stored Lesson Topic pointer known, or destination will be
  checked in Review. Exact existing/new/ambiguous/unavailable status appears
  only after Step 3's explicit Notion check.

### Step 2 — Content

Two top-level choices are shown:

1. **Rebuild the whole homework** — selected by default. Every content phase is regenerated with
   current prompts. The source extract is reused unless the extraction switch is enabled.
2. **Choose specific parts** — opens the existing phase picker. The server remains authoritative
   for dependency expansion.

Selective mode shows the real expansion in operator language: “Your selection rebuilds 10 of 11
content phases.” Automatically affected phases are included by default. Excluding an affected phase
is under **Advanced** and continues to require a fresh consistency acknowledgement.

Advanced contains:

- content model selection;
- extraction refresh;
- affected-phase exclusions; and
- resolved judge, solver and transport details as read-only information.

The model initially comes from the current launch defaults (`gemini-3.6-flash` in the tested
environment), rather than forcing the operator to understand the model manifest on every campaign.
Changing it remains possible. The estimate/review must refuse a launch contract that no currently
active worker can claim; it must not allow another permanently pending campaign such as the tested
content/solver self-match without a Claude API credential.

### Step 3 — Review

The review is the last non-spending screen. It shows:

- campaign version (`Homework V3`);
- lesson and canary counts;
- regenerated versus copied phase counts;
- extract reused/refreshed;
- content model;
- estimated cost range;
- worker-executability result; and
- Notion destination result with a link when an existing Lesson Topic will be reused.

Cost/phase/worker estimation remains DB-only and reactive. Notion resolution is
not part of that query: Step 3 has a deliberate **Check Notion destinations**
action that runs once for the current lesson/language/version selection. Any
change to those destination-relevant inputs invalidates the result and requires
a fresh check; phase, model and canary-size edits do not repeat Notion reads.
The check scans every selected target up to the existing campaign cap and says
that a large campaign may take minutes. It never silently leaves targets
unchecked.

The primary button reads **Create campaign and start N canary lesson(s)** and is explicitly marked as
the first paid action. The frontend may orchestrate the existing create and canary endpoints, but it
must treat partial success correctly: once campaign creation succeeds, the draft is cleared and any
canary-launch failure becomes a retry action on that campaign, never a second campaign creation.

### Step 4 — Canary

While generation runs, this step shows phase progress, judge state, spend and any actionable error.
When the canary finishes, the operator can open the generated homework and judge report before
choosing:

- **Approve canary and continue**; or
- **Reject and stop** with an audit reason.

Approval retains current behavior: successful canary targets become publication-eligible, remaining
targets launch in bounded waves, and each successful target publishes automatically. A one-target
campaign completes from this same approval; it never renders an empty “approve bulk” action.

## Browser-persistent draft

Use a versioned local-storage record, for example `hcga.regeneration.draft.v1`. Store only operator
input and the active step:

- subject and grade filters;
- selected book and output language;
- selected lesson IDs;
- full/selective mode;
- requested and excluded phases;
- extraction choice;
- provider/model override;
- campaign publication version;
- explicit Lesson Topic choices made to resolve ambiguous Notion matches;
- canary size; and
- active wizard step.

Do not store server-derived phase plans, estimates, manifests, prices, eligibility rows or Notion
resolution results. Those are re-fetched and recalculated after hydration.

On restore:

1. parse and schema-check the record without throwing;
2. discard unknown fields and migrate or reset unsupported schema versions;
3. re-fetch books, eligibility, models, plan and the DB-only estimate; keep the
   Notion destination result empty until the explicit Step 3 check;
4. remove lessons and phase names that are no longer eligible/valid, tell the
   operator what changed, and return an emptied selective phase set to full mode;
5. reset dependency-exclusion acknowledgement to false so a restored warning is consciously
   re-approved; and
6. clamp canary size to the surviving target count.

Storage failures (private mode, quota, corrupt JSON) degrade to an empty draft and a non-blocking
message. They never block regeneration. **Discard draft** removes the record and returns to Step 1.
Successful campaign creation clears it. Cancelling or rejecting an already-created campaign does
not resurrect it.

## Campaign-wide publication version

Add an immutable `publication_version` (integer, `>= 2`) to the campaign specification and create
request. Every target in the campaign uses that exact number.

The source version and campaign version are different facts: a V1 source may produce V3. Skipping V2
is allowed. Provenance continues to record the actual source job and source publication version.

Before campaign creation, reject a target when:

- its latest source version is greater than or equal to the requested campaign version;
- the database already consumed that version for the same lesson and output language; or
- Notion already contains `Homework V{n}` for the resolved Lesson Topic.

A new campaign never adopts a same-title page created by another or unknown campaign. Only a retry
of the same already-created target may adopt a page whose complete marker matches that target's
campaign, revision job, lesson, language and version.

The existing partial unique index on `(toc_entry_id, output_language, publication_version)` remains
the final database fence. Version reservation retains the advisory lineage lock and retry stability,
but reserves the campaign's requested number instead of calculating `max + 1`. Once reserved, the
number remains consumed under the existing failure/cancellation rules.

The review screen reports all version conflicts before the first model call. A later race still
fails closed at reservation/publication and becomes an actionable campaign error; it never chooses a
different number silently.

## Notion Lesson Topic resolution

The current cheap preflight only verifies the subject-page mapping. The publisher later decides
whether a stored pointer belongs to the correct language tree and may create a title-disambiguated
Lesson Topic. In the test, initial V1 seeding created a suffixed Lesson Topic beside an existing
unsuffixed page; regeneration correctly reused the stored pointer, but the pointer itself represented
the wrong duplicate.

The review preflight must therefore resolve the actual destination, read-only, before spend:

1. Resolve the language-specific subject page and `Generated Homeworks` container.
2. If the stored `toc_entries.notion_lesson_page_id` is a child of that container, use it.
3. Otherwise search the container using the canonical lesson identity plus normalized base title,
   section number and known disambiguators.
4. Exactly one safe match is adopted and shown in green with its link.
5. No match is shown explicitly as “A new Lesson Topic will be created.” It is never described as an
   already-known destination.
6. Multiple plausible matches are an ambiguity, not permission to create another page. Campaign
   creation is blocked until the operator selects the correct existing page or abandons that target.

The estimate request carries any operator-selected destination overrides as structured
`(toc_entry_id, output_language, notion_lesson_page_id)` inputs. The server validates that every
chosen page is one of the safe candidates returned for that exact lineage and is still a child of
the reviewed language/subject/grade container. An override is operator input, so it is included in
the browser draft; candidate lists and resolved statuses remain server-derived and are re-fetched.
Changing the book, language or selected lessons prunes overrides that no longer belong to the
selection.

The resolved `Generated Homeworks` container decision and Lesson Topic parent
page ID (or their explicit create-new decisions) are frozen on the regeneration
target so publication uses exactly what the operator reviewed. The publisher
revalidates both membership edges before writing. It does not redo a looser
container/title decision that could diverge from review.

Review and publication use `notion.page_creator._normalize` as their one title
normalizer, including its trailing `(N)` folding. A create-new decision that
later sees exactly one normalized match adopts it; multiple normalized matches
fail closed as ambiguous. Remote Notion reads occur only after all required DB
facts have been copied out and the DB session/row locks are closed. Canary-start
revalidation follows the same two-stage rule and never holds campaign or target
locks during HTTP.

Remote reads should be cached per language/subject/grade container during one preflight so a bulk
campaign does not perform one full child scan per target. Rate limits surface as a retryable preflight
error before model spend.

## API and data boundaries

Expected interface changes:

- Campaign create/estimate schemas accept and return the campaign publication version.
- Eligible/estimate responses distinguish source version from requested campaign version.
- Estimate/review returns worker executability and resolved Notion destination data per target plus
  rollups.
- `/estimate` remains DB-only. A separate destination-check endpoint performs
  the explicitly triggered read-only Notion scan and returns its digest.
- Regeneration targets persist the reviewed `Generated Homeworks` container and
  Lesson Topic parent, or each explicit create-new decision.
- Campaign reports always show the campaign version and the actual published page link.

The exact migration number is chosen only after rebasing onto the current single Alembic head. This
design must not assume that today's local `0063` remains available.

## Error handling

- No eligible lesson: remain on Step 1 with the server's reason.
- Restored stale lesson: remove it, explain that it is no longer eligible, keep the rest.
- No worker can execute the contract: block Step 3 and suggest a compatible content model.
- Missing PDF with extraction enabled: block before campaign creation when local availability can be
  known; otherwise show a retryable generation error without consuming a publication version.
- Missing Notion subject mapping: block before spend.
- Notion disabled or uncredentialed on the head: block destination check and
  campaign creation with a plain configuration message; generation cannot be
  approved for automatic publication without a verified publisher.
- Ambiguous Lesson Topic: block before spend and show the candidates.
- Existing campaign-version collision: block before spend.
- Campaign created but canary launch failed: navigate to that campaign and offer **Retry canary**.
- Judge failure: preserve the existing bounded retry/fallback and report its final state; do not add a
  second approval model here.
- Publication failure after approval: retain existing retry/abandon behavior and audit trail.

## Testing and acceptance

### Frontend

- Draft round-trip through local storage, including browser-restart hydration.
- Corrupt/old storage degrades safely.
- Stale lessons/models are pruned and acknowledged exclusions are reset.
- Whole rebuild is the default and extraction is off.
- Selective mode renders server dependency expansion and hides exclusions under Advanced.
- Books beyond the first 100 can be selected.
- One click cannot create two campaigns when canary launch fails.
- Single-target canary approval has no empty bulk gate.
- Build and existing regeneration frontend suites remain green.

### Backend/database

- Campaign version may skip from a V1 source to V3.
- Every target in one campaign receives the same requested version.
- Lower/equal, consumed and concurrent version conflicts fail closed.
- Retry keeps the same reserved number.
- Destination resolution reuses a valid pointer, adopts one safe normalized match, marks no-match as
  create-new, and blocks ambiguity.
- Publisher uses the frozen reviewed parent and revalidates membership.
- Worker-capability preflight catches the tested self-solver/no-Claude combination.
- Fresh PostgreSQL migration from the current head and full regeneration integration suites pass.

### Isolated end-to-end gate

Before any merge into `Nggaev-v2`, run in a separate branch/worktree with a
test-specific environment and a dedicated Notion sandbox subject page. The
sandbox contains one known Lesson Topic and is the only remote tree the test may
mutate; using a curriculum/production subject page requires a separate explicit
user approval after the exact target tree is shown.

1. restore a browser-persisted draft;
2. select a book outside the original first-100 window;
3. launch a full V3 canary with `gemini-3.6-flash`;
4. verify all 11 content phases are regenerated and extraction is reused by default;
5. confirm the reviewed existing Lesson Topic is the actual parent;
6. confirm exactly one new `Homework V3` page and no duplicate Lesson Topic;
7. inspect the generated homework and judge report;
8. approve the canary and verify automatic publication/completion; and
9. record real token cost, Notion IDs and cleanup instructions.

## Rollout

Keep the feature flag off outside the isolated test environment. Land the UX/version/destination
changes on a dedicated branch stacked on the fully integrated regeneration head. Obtain code review,
run offline/fresh-DB/frontend verification, then perform the isolated live canary above. No push, PR
update, merge, deployment or production flag change is implied by design approval.

## Collision-gate record

Before this document was created, all remotes were fetched and pruned; local/remote branches,
worktrees and open PRs were inspected. Open PRs `#136`, `#131`, `#128`, `#118`, `#117` and `#108`
are all authored by `AdxamAxatov`; no project-manager-owned PR was touched. The relevant prior UI
work (`feat/regen-wave4-ui` and `fix/regen-final-ui-state`) is already integrated into
`test/regen-v3-local@ace86c0a`. Its “preserve regeneration action state” behavior preserves mutation
feedback during polling; it has no local/session-storage draft persistence. This design builds after
that integrated work and does not edit its source branches.
