# Versioned homework regeneration — operator runbook

**Status: shipped, dormant.** The code is in the branch, both backend flags default
to `false`, and the SPA is built with the regeneration area hidden. Nothing has been
enabled anywhere, no production campaign has been run, and no paid model call or live
Notion write has been made by this feature. Turning it on is a **separate operator
decision** — see [Acceptance status](#12-acceptance-status-what-has-and-has-not-been-proven).

> Design: `docs/superpowers/specs/2026-08-20-versioned-homework-regeneration-design.md`
> Plan: `docs/superpowers/plans/2026-08-20-versioned-homework-regeneration-implementation.md`

---

## 1. What it does, in one picture

Regeneration re-runs an already-finished homework lesson with today's prompts and
publishes the result as a **new page next to the old one**:

```text
Lesson Topic
├── Homework      ← V1. The original. Never touched by this feature.
├── Homework V2   ← first approved regeneration
└── Homework V3   ← the next one
```

The existing `Homework` page is logical **V1**. It is never renamed, cleared,
rewritten or deleted. Neither is any earlier `V2`/`V3`. Every regeneration only ever
**adds** a sibling page. There is no "replace the current homework" mode, by design.

Version numbers start at 2 (V1 is the pre-existing page and has no database row), and
they are counted per **lesson + output language**. Uzbek V2 and Russian V2 of the same
lesson are independent publications, each with their own sequence.

## 2. Prompts: you always get the currently deployed ones

There is no prompt-set picker anywhere in this feature. A campaign always uses the
prompt files deployed with the running application (`prompts/_general/`). Old prompt
text lives in Git history only.

What a campaign records for the audit trail: the application Git revision, the
resolved prompt hash of every regenerated phase, and — for phases that were copied
rather than re-run — a link back to the exact source phase row they came from.

Practical consequence: **deploy the prompts you want first, then run the campaign.**
The feature adds no draining or mixed-prompt handling; the operator is expected to
ensure no ordinary generation is mid-flight during a prompt cutover.

## 3. Turning it on — the three flags

Three separate switches. All three must be right or you will see a partial, confusing
state rather than an error.

| Flag | Where | Default | What it gates |
|---|---|---|---|
| `REGENERATION_ENABLED` | backend env, head/API process | `false` | The whole feature. Off ⇒ every `/api/v1/regeneration*` route returns **404** (not 403 — a stale browser tab must not be able to tell the routes exist). |
| `REGENERATION_PUBLISHER_ENABLED` | backend env, **head/API process only** | `false` | The loop that writes `Homework V{n}` pages to Notion. Independent so generation can be exercised with delivery dark — but **not sufficient alone**: `main.py` starts the loop only when `REGENERATION_ENABLED` **and** this flag are both true, so setting this one with the master flag off starts nothing and logs no start line. |
| `VITE_REGENERATION_ENABLED` | **SPA build**, not runtime | unset | Whether the SPA has the `Regeneration` nav item and the `/regeneration` route compiled into it. |

### 3a. The frontend flag has exactly one working value: `1`

`isRegenerationEnabled` (`web/src/lib/regeneration-feature.ts`) matches the **literal
string `"1"`** and nothing else:

```bash
cd web
VITE_REGENERATION_ENABLED=1 npm run build     # correct
```

`true`, `TRUE`, `yes`, `on`, `enabled` all evaluate to **off**. There is no warning,
no console message and no error — the navigation item and the route simply are not in
the bundle, and the operator reports "the feature did not ship". If the nav item is
missing, check this value first.

The value is **baked in at build time**, not read at runtime. Changing it means
rebuilding the SPA and reloading; editing `.env` and restarting the API does nothing
for the frontend.

Also note the shipped `Dockerfile` runs a plain `npm run build` with no
`ARG`/`ENV` for this variable, so **an image built as-is always ships the
regeneration UI hidden**. On the current bare-metal head, build the SPA on the host
(`cd web && VITE_REGENERATION_ENABLED=1 npm run build`) — FastAPI serves `web/dist`.

### 3b. Ordering: approval needs the publisher already on

Two routes refuse with a `409 publisher_disabled` while
`REGENERATION_PUBLISHER_ENABLED` is off, because they queue delivery work that nobody
would serve:

- `POST /regeneration/campaigns/{id}/approve`
- `POST /regeneration/targets/{id}/retry-publication`

So the flag-on order for a real campaign is: **schema → backend flag → publisher flag
→ SPA rebuild.** You *can* deliberately run with the publisher off to exercise
drafting, estimation and canary generation — you just cannot approve until you turn
it on.

### 3c. Schema and process ownership

- **Migration 0063** (`0063_regeneration_campaigns`) must be applied to the shared
  database: `uv run alembic upgrade head`. That is one migration on the shared DB, not
  a per-host step.
- **The publisher runs on the head/API process only, and needs BOTH backend flags.** It
  starts from `main.py`'s lifespan — guarded by `if settings.regeneration_enabled and
  settings.regeneration_publisher_enabled` — after the startup reconcile, the
  version-floor stamp and the LISTEN bus, beside the embedded worker.
  `REGENERATION_PUBLISHER_ENABLED=true` with `REGENERATION_ENABLED` still false starts
  **no** loop and prints **no** `Regeneration publisher started` line; nothing warns you.
  The claim protocol is safe if two processes accidentally run it, but enable it on one
  designated head anyway.
- **Worker PCs need neither regeneration flag.** They run a campaign's revision jobs as
  ordinary queue work and reconcile the outcome onto the campaign target. What they
  **do** need is **current code**, because a revision job is a `homework_jobs` row with
  new columns — a worker on a stale build against a migrated database is the failure
  mode to avoid here.

All API paths below are relative to `/api/v1` (so `POST /regeneration/campaigns` is
`POST /api/v1/regeneration/campaigns`). Auth is the ordinary operator bearer token; an
anonymous request fails authentication *before* the feature gate, so it never learns
whether the routes are hidden.

### 3d. Publisher tuning knobs (`app/config.py`)

| Setting | Default | Means |
|---|---|---|
| `REGENERATION_PUBLISHER_INTERVAL_SECONDS` | 30 | Idle sweep interval. A pass that did work loops straight back, so a backlog drains at Notion's pace, not at this interval. |
| `REGENERATION_PUBLISHER_LEASE_SECONDS` | 300 | Durable claim lease. Keep it well above a realistic Notion write (page + children + PDF upload). |
| `REGENERATION_PUBLISHER_MAX_ATTEMPTS` | 5 | Automatic delivery attempts before a target parks in `publication_failed` for a human. |
| `REGENERATION_PUBLISHER_BACKOFF_BASE_SECONDS` / `_MAX_SECONDS` | 60 / 3600 | Exponential backoff between attempts, and its ceiling. |
| `REGENERATION_LAUNCH_WAVE_SIZE` / `_INTERVAL_SECONDS` | 4 / 60 | Launch stagger for the bulk wave. Deliberately more conservative than the Fleet batch pair (6/60) because a regeneration wave re-runs whole snapshots on top of whatever the fleet is already doing. Either at `0` disables staggering. |

**Delivery is serial.** One publisher pass delivers **at most one** page, then loops.
This is intentional pacing against Notion, not a bug — a 40-target campaign publishes
one page at a time.

**Drain caveat.** On shutdown the publisher gets a 30-second grace window to finish
the target it is on; past that it is cancelled. That is safe but not instant: a
cancelled mid-delivery target keeps its lease until
`REGENERATION_PUBLISHER_LEASE_SECONDS` expires, and only then can another pass pick it
up and adopt the half-written page by its marker. Do not expect a restarted head to
resume a killed delivery immediately — expect it after the lease window.

### 3e. API transport only

A campaign is refused unless the **effective** transport of the content role and every
other role resolves to `api`. A role left at `inherit` follows the campaign's own
transport, so `inherit` over a `cli` contract is still a `cli` call and is still
refused. This matches the standing decision that all real generation runs
`transport=api`.

## 4. Phases: what gets re-run, what gets copied

You pick the phases you want regenerated. Everything downstream of them in the
dependency graph (`flows.PHASE_DEPS`) is **automatically included**, because a phase
authored against an old upstream output would otherwise contradict the new one.
Everything else is **copied** from the source homework — same content, same judge
status, zero model cost, with a link back to the row it came from.

The result is always a **complete snapshot**: one terminal row for every phase of the
lesson. A partial revision can never publish.

### 4a. The cascade is bigger than it looks

Measured against the current 11-phase flow:

| You select | Phases actually regenerated |
|---|---|
| `flashcards` | **10 of 11** — flashcards, memory-check, all four `practice-*` games, practice-rlc, practice-error-detection, boss-arena, reflection |
| `memory-check` | **5** — memory-check, practice-error-detection, practice-memory-match, boss-arena, reflection |
| `boss-arena` | **2** — boss-arena, reflection |
| `reflection` | **1** — reflection only |

Selecting an early phase is **not** a cheap, isolated change. The preview
(`POST /regeneration/phase-plan`) shows the real expansion before you spend anything —
read it.

### 4b. The exclusion override

You may drop an auto-included downstream phase back to "copied". This deliberately
creates a homework where copied content was authored against an **older** version of
an upstream phase, so the packet may be internally inconsistent.

The API refuses this until you resubmit with `exclusion_acknowledged: true`. The
exclusion and the acknowledgement are frozen into the campaign record.

Use it when you know the downstream phase does not actually depend on what changed.
Do not use it to make an estimate look cheaper.

### 4c. Extraction is off by default

By default the lesson `extract` is **copied** from the source job — free, with
provenance recorded as the existing zero-cost cache marker.

Turning extraction on (`refresh_extraction: true`) re-reads the PDF and, because every
content phase depends on the extract, **automatically includes every content phase**.
That is close to a full regeneration. The exclusion override still exists, but the
estimate will show the real cost. Leave it off unless the extract itself is what is
wrong.

## 5. Running a campaign

The operator area is its own page (`/regeneration`) with its own campaign list. The UI
always says whether you are **Generating** (the Fleet launcher) or **Regenerating** —
they are never mixed.

### Step 1 — Pick lessons and preview

`GET /regeneration/eligible` lists regenerable lessons with, per language, the current
source version and the next expected version — plus an explicit "why not" for every
lineage it left out.

A source must be a **completed homework job with a complete phase snapshot**. Failed,
cancelled, partial and teacher-material jobs cannot be sources. For V3 and later, the
default source is the latest successfully published revision in that language;
otherwise it is the original V1 job.

### Step 2 — Estimate

`POST /regeneration/estimate` prices the plan. Copied phases cost zero. Regenerated
phases are priced from **observed** successful API usage over the previous **30 days**
for the same operation/phase/provider/model; where there is no history, a conservative
static token envelope is used and labelled as such. The high estimate adds the
configured schema-retry and judge/solver regeneration budgets.

These are **estimates**. The canary screen later shows estimated vs **actual** cost
before you commit to the rest.

Creating or estimating a campaign makes **no model call and no Notion page**.

### Step 3 — Create the campaign

`POST /regeneration/campaigns` freezes an immutable record: the selection, the phase
plan per target, exclusions and acknowledgement, the extraction choice, canary
membership, and a fully **resolved** launch contract (every role's provider/model/
transport and the session-limit strategy are concrete values, read once, at this
moment).

That last part matters: a campaign runs in two waves separated by a human gate. If the
contract were re-resolved after approval, an operator editing the global launch
defaults in between would give one campaign two different meanings and the approval
record would no longer describe what actually ran. So it is read once and copied
thereafter — never re-resolved.

### Step 4 — Launch the canary

Before any spend, a Notion preflight checks that **every** target — canary or not —
has a reachable Lesson Topic destination. Missing destinations come back as one
actionable list and block the launch.

Then only the **canary** targets get revision jobs. Non-canary targets stay as plan
rows with no job, so no worker can start bulk work before the human gate.

### Step 5 — Review the canary

The canary review gives you, per canary lesson: a link to the complete revised
homework and its download, the copied-vs-regenerated phase counts, and the judge and
solver status tallies — plus, at campaign level, the **actual** spend (built only from
`agent_usages` rows belonging to this campaign's revision jobs) next to the estimate
you approved. Nothing has been written to Notion at this point.

### Step 6 — Approve or reject

**Reject** (`POST …/reject`, reason required): the campaign ends. Canary revisions
stay in the app for audit. No Notion page is created and **no version number is
consumed**. To try again you create a new campaign.

**Approve** (`POST …/approve`, publisher must be on): this is the **only** human
quality gate in the feature. Approval, exactly once:

1. makes successful canary targets eligible for automatic publication;
2. releases the remaining targets for generation (staggered by the wave knobs);
3. makes each later successful target publish automatically as soon as its complete
   snapshot is ready.

**There is no per-lesson publication approval after this.** Once you approve, every
remaining lesson that generates successfully will be published without asking you
again. Repeating the approval request creates nothing twice.

For a **single-lesson** campaign, that lesson *is* the canary: generate → review →
approve → publish, with no empty bulk step.

### Step 7 — Read the report

Targets are reported in six buckets, and in-flight work is always shown rather than
silently omitted:

| Bucket | Meaning |
|---|---|
| `published` | Delivered. Terminal. Has a version and a Notion page link. |
| `publication_pending` | Generated and queued for delivery. |
| `publication_failed` | Generated; delivery failed. **Needs you.** |
| `generation_failed` | Never produced a usable snapshot. **Needs you.** |
| `abandoned` | Explicitly given up on. Terminal. |
| `in_flight` | `planned` / `generating` / `awaiting_canary_approval` / `publishing`. |

A campaign cannot report terminal completion while any target is attention-required;
it stays `attention_required` until every failed target is either retried to
`published` or explicitly abandoned. A finished campaign distinguishes
`completed` (all published) from `completed_with_abandonments`.

## 6. Judge results: soft warnings vs hard failures

Regeneration preserves the existing pipeline's judge behaviour exactly. It does **not**
add a stricter publication gate.

**Soft — visible, but they publish.** These leave a complete, usable snapshot:

| `judge_status` | Means |
|---|---|
| `unavailable` | The judge could not be reached (retried once). The phase content is kept ungraded. |
| `refused` | The judge hit a content-policy refusal. Not retried. |
| `major_shipped` | Major findings survived the `max_judge_regens` budget; the best artifact was kept. |
| `major_regen_failed` | The repair generation itself failed; the best available artifact was kept. |

These are shown prominently in canary review and in the campaign report, and they are
the main thing you are looking at during canary review — but after approval they do
**not** block automatic publication. If a soft warning is unacceptable to you, that is
a reason to **reject the canary**, not something the system will stop for later.

**Hard — these cannot publish.** A real phase-generation failure, a missing required
phase, an invalid structured artifact, an authentication/configuration failure, or the
terminal solver outcome `mismatch_blocked` (a blocked answer-key mismatch) all leave
the target in `generation_failed`.

Copied phases keep their recorded judge status, are not re-judged, and cost nothing.

## 7. When something goes wrong

Generation state and publication state are tracked separately, on purpose: a revision
is **never** regenerated just because Notion delivery failed.

### Retry generation — `POST /regeneration/targets/{id}/retry-generation`

Re-runs a failed revision on its **existing** snapshot and phase plan. No publisher
flag needed. Refused with a visible `409` if the campaign's frozen contract pins a
model that has since been retired.

### Retry publication — `POST /regeneration/targets/{id}/retry-publication`

Re-queues delivery and buys a fresh automatic attempt budget. It **never** calls a
model, never creates a revision job, never allocates a new version, and never touches
V1 or any earlier version page. It is idempotent across timeouts and crashes: the
publisher uses the stored page id when it has one, otherwise looks for the exact
immutable marker and adopts the page a crash left behind, and only creates a page when
neither exists. Requires the publisher flag.

### Abandon — `POST /regeneration/targets/{id}/abandon`

Give up on one target. Requires a reason, is audited, and **never deletes a Notion
page**. Idempotent on an already-abandoned target; refused on a `published` one — a
delivered page is history, not work in progress.

Abandonment is an operational failure resolution, not a content decision. Use it when
a target is genuinely stuck and you want the campaign to be able to finish.

### Cancel the campaign — `POST /regeneration/campaigns/{id}/cancel`

Stops planned targets from launching, requests cancellation of active generation
through the normal safe path, and abandons complete-but-unclaimed publication targets.
Already-published pages remain. Any version reserved once publication began remains
consumed. **No cancellation path deletes a Notion page.**

## 8. `VersionPageCollision` — read this before it happens

The publisher only adopts a page it can **prove** is ours: it validates a
five-field machine-readable marker (lesson/TOC entry, output language, revision job,
campaign, version) inside the page. Title matching alone is never enough.

If a page titled `Homework V{n}` exists under the Lesson Topic but its marker is
missing or belongs to something else, that is a `VersionPageCollision`. The publisher
**never** clears, overwrites or silently adopts it, and marks the target
`publication_failed` as **non-retryable**.

**This means the automatic path is permanently stuck for that version.** Pressing
retry will raise the same collision forever. You have exactly two exits:

1. **Clean up the offending Notion page by hand**, then retry publication. The target
   will then create or adopt the correct page at the same reserved version.
2. **Abandon the target.** This leaves the real, populated page sitting in Notion with
   no `published` row behind it, and **permanently consumes that version number** —
   the reserved number is preserved, never released. The next successful regeneration
   of that lesson publishes at the *following* version (an abandoned V3 means the next
   one is V4, and there will be a gap).

Neither exit is automatic, and there is no third option. Decide deliberately.

### Version numbers, generally

- A version is reserved **atomically when delivery first begins** — not at campaign or
  canary creation.
- A rejected canary, a never-started target and a generation failure all consume
  **nothing**.
- Once publication has begun, that number is **never reused** — not by a failed
  delivery, not by a cancellation, not by a later campaign.
- Every retry of the same target publishes at the **same** reserved version and page
  identity.

## 9. Cost caps — both scopes, precisely

This is the part that surprises people, so it is stated plainly:

- Revision jobs carry `batch_id=NULL` (the database refuses a revision with a Fleet
  batch id). The per-batch cap `COST_CAP_BATCH_USD` is computed by joining usage rows
  to a batch, so **a regeneration campaign is subject to no individual batch cost cap
  at all**, however large it is.
- The fleet-daily cap `COST_CAP_FLEET_DAILY_USD` sums **all** API usage in a rolling
  24 hours with no job-kind filter, so **regeneration usage does count against it** —
  and a large campaign can trip it and **pause ordinary API batches fleet-wide**.

Budget and stage campaigns accordingly. Actual campaign cost is derived only from
`agent_usages` rows attached to revision jobs; copied phases never duplicate source
usage rows, and a publication retry never adds model cost.

## 10. Regeneration history makes the source undeletable

A target row records a version number that is consumed forever, so no delete may
cascade it away and silently free that number for reuse. **Most** of migration 0063's
foreign keys are therefore `ON DELETE RESTRICT` — but **not all of them**, and the one
exception is deliberate:

| Foreign key | On delete | Why |
|---|---|---|
| `fk_regeneration_targets_toc_entry_id` | `RESTRICT` | the lesson a consumed version belongs to cannot vanish |
| `fk_regeneration_targets_campaign_id` | `RESTRICT` | a target never outlives the campaign spec that froze it |
| `fk_homework_jobs_revision_of_job_id` | `RESTRICT` | a source snapshot cannot be deleted out from under a live revision |
| `fk_homework_jobs_regeneration_target_id` | `RESTRICT` | a revision job never outlives its target |
| `fk_phase_outputs_copied_from_phase_output_id` | `RESTRICT` | a copied phase keeps its provenance |
| **`fk_regeneration_targets_source_job_id`** | **`SET NULL`** | **intentional** — campaign reporting and the consumed version survive a correctly ordered child-first purge |

So while regeneration history exists for a lesson, these routes refuse with a
structured **409** naming what blocks them, rather than a raw database error:

| Route | Error code |
|---|---|
| delete a book | `book_delete_blocked_by_regeneration` |
| delete a TOC entry | `toc_entry_delete_blocked_by_regeneration` |
| re-extract a book's TOC (`/toc/retry`) | `toc_retry_blocked_by_regeneration` |

**Those 409s do not depend on the asymmetry above.** They are raised ahead of the
database by `history_for_toc_entry` / `history_for_book`, which refuse on **any** target
row referencing the lesson, terminal or not — so the operator-facing answer is unchanged
by `source_job_id` being nullable.

**What the `SET NULL` half means for a future child-first purge.** Because
`revision_of_job_id` is `RESTRICT` while `source_job_id` is `SET NULL`, a source job can
only be deleted *after* its revision child already is — the database enforces that
ordering rather than trusting the operator to follow it. Once the ordering is followed,
deleting the source **succeeds** and blanks the target's `source_job_id` instead of
blocking: the row keeps its status, its permanently consumed `publication_version` and
its `notion_page_id`, but retains no snapshot behind it and nothing to regenerate from.
`lineage_targets_missing_source` (`source_job_id IS NULL`) is precisely the detector for
that state, and its callers refuse rather than plan a regeneration with no source.

No such purge tool ships in this release, and nothing above makes a book or lesson
deletable today — while regeneration history exists, the answer is still "you cannot
delete this yet". Cancelling or abandoning a campaign does **not** erase the audit
history and does not unblock these routes.

## 11. Verifying it is off, and rolling back

### Verify flag-off

```bash
# Backend: every regeneration route must 404 (not 401/403/500)
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  http://<head>:8000/api/v1/regeneration/campaigns          # expect 404

# Head log on boot: with the flags off there is NO "Regeneration publisher started" line
grep -c 'Regeneration publisher started' var/server.log      # expect 0

# SPA: no "Regeneration" nav item, and /regeneration does not resolve to the page
```

Also confirm ordinary operation is untouched: Fleet batch launch, batch adoption and
resume, per-job archive retry, teacher-material archival and TOC status enrichment all
exclude revision jobs by construction, so with the flags off there is nothing for them
to see.

### Rollback

**Turn both backend flags off and restart the head.** That is the whole mechanical
rollback:

- no publication loop starts;
- every regeneration route returns 404;
- in-flight revision jobs already in the queue still finish as ordinary jobs (they are
  `homework_jobs`), and they are **never** archived to the legacy `Homework` page —
  `notion_archive.archive_job` intrinsically refuses any job with
  `revision_of_job_id IS NOT NULL` regardless of `force`, claim token or caller;
- **every existing `Homework`, `Homework V2`, `Homework V3` page in Notion is
  untouched.** Rollback deletes nothing.

Rebuild the SPA without `VITE_REGENERATION_ENABLED=1` to remove the UI as well.

**But rollback is not free for a lesson caught mid-campaign — it strands the lineage.**
`uq_regeneration_targets_active_lineage (toc_entry_id, output_language) WHERE terminal_at
IS NULL` permits at most one **non-terminal** target per lineage across all campaigns,
and only `published` and `abandoned` stamp `terminal_at`. So every target left in
`planned`, `generating`, `awaiting_canary_approval`, `publication_pending`, `publishing`,
`generation_failed` or `publication_failed` at the moment you flip the flags keeps
holding its lineage — while the three routes that could clear it
(`retry-generation`, `retry-publication`, `abandon`) are **404 with the feature off**.
Those lessons are therefore **blocked from any new campaign until the flags are turned
back on** and an operator drives each target to `published` or `abandoned`.

Nothing is lost or corrupted by that, and it never touches Notion — the stranding is a
uniqueness fence, not damage. But if you expect to re-launch soon, drain or `abandon`
in-flight targets **before** flipping the flags off, not after.

Leaving migration 0063 applied is fine and is the recommended rollback state — the
tables are simply unused. Do not downgrade it while any campaign row exists.

## 12. Acceptance status: what has and has not been proven

**Proven, locally, against fakes.** The regeneration test suites run entirely against
fake providers and a local database — pure planner/state/estimator tests, migration
and constraint tests, repository concurrency and publication-claim tests, service and
pipeline isolation tests, and API/schema tests. No paid model call, no production
database and no live Notion write was involved in any of it.

**What that proof does and does not cover, as of this commit.** Those suites exercise
the feature component by component. The **full fake end-to-end lane** — one campaign
driven across its entire lifecycle (draft → estimate → canary → approve → publish) in a
single test — is being built in parallel as **Task 11**, and **is not part of this
commit**. So read the paragraph above as "every component is covered against fakes",
not as "a whole-lifecycle run has been executed here". This is a statement about what
this commit contains — not a finding that the Task 11 lane failed or was skipped. When
it is integrated, update this paragraph to name the lane and record its result.

**Not proven, and explicitly outstanding.** CLAUDE.md's real-generation acceptance gate
has **not** been satisfied for this feature. A bounded, separately authorized sample
campaign against real models and real Notion is still owed before the feature can be
considered operationally accepted. Neither a green local suite nor a branch review
substitutes for that gate.

**Not authorized by shipping this code:** enabling either flag in production, running
a sample or production campaign, deploying a prompt change, making a paid model call,
or writing to live Notion. Each is a separate operator decision.

### Suggested first-enable sequence, when it is authorized

This is the one order used throughout this runbook and in `docs/DEPLOY.md` — **schema
→ `REGENERATION_ENABLED` → `REGENERATION_PUBLISHER_ENABLED` → SPA rebuild** (§3b). The
UI goes last on purpose: it is the only step that puts an `Approve` button in front of a
human, and approving before the publisher is on can only answer `409 publisher_disabled`.

1. Apply migration 0063 to the shared database.
2. Enable `REGENERATION_ENABLED` on the head only; confirm routes answer and ordinary
   Fleet generation and archival are unchanged.
3. Enable `REGENERATION_PUBLISHER_ENABLED` on that same one head and restart it. The
   publisher loop requires **both** backend flags together, so confirm the
   `Regeneration publisher started` log line rather than assuming this flag alone
   started it.
4. Rebuild the SPA with `VITE_REGENERATION_ENABLED=1`; confirm the nav item appears.
5. Run **one single-lesson campaign** on a lesson you are willing to spend on. Review
   the canary against the estimate, approve, and confirm exactly one `Homework V2`
   sibling appears with V1 untouched.
6. Only then consider a larger campaign — and check the fleet-daily cap headroom
   first (§9).
