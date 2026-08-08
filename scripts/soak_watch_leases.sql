-- Fenced-lease soak watch (PR #121 / worklog 0163, migration 0052).
-- READ-ONLY: every statement is a SELECT. Safe to run against production at any time.
--
--   psql "$DATABASE_URL_PSQL" -f scripts/soak_watch_leases.sql
--
-- Three signals the #121 review named, plus the two invariants the lane's own
-- design notes call load-bearing. Each block prints what it means and what a
-- BAD reading looks like, so the output is self-interpreting at 3am.
--
-- Event vocabulary is app/services/lease.py:42-49 — do not invent event names.

\pset border 2
\echo ''
\echo '=============================================================='
\echo ' SIGNAL 1 — lease_lost rate (must be RARE and must not trend up)'
\echo ' A lost lease means a worker discovered mid-flight that its job'
\echo ' had been reclaimed. Rare = the fence doing its job. Sustained or'
\echo ' rising = workers running STALE CODE, or a reclaim storm.'
\echo '=============================================================='
select date_trunc('hour', created_at) as hour,
       count(*) filter (where event_type = 'claimed')       as claimed,
       count(*) filter (where event_type = 'lease_lost')    as lease_lost,
       round(100.0 * count(*) filter (where event_type = 'lease_lost')
             / nullif(count(*) filter (where event_type = 'claimed'), 0), 2) as lost_pct
from job_lease_events
where created_at > now() - interval '24 hours'
group by 1 order by 1 desc limit 24;

\echo ''
\echo '=============================================================='
\echo ' SIGNAL 2 — reclaim mix: stale (heartbeat aged out) vs forced'
\echo ' (deadline). FORCED should be the rare one. A rising forced share'
\echo ' means workers are alive but not heartbeating — the exact state'
\echo ' the fence protects against but does not fix.'
\echo '=============================================================='
select event_type, count(*) as events,
       count(distinct job_id) as jobs,
       min(created_at) as first_seen, max(created_at) as last_seen
from job_lease_events
where created_at > now() - interval '24 hours'
  and event_type in ('reclaimed_stale', 'reclaimed_forced')
group by 1 order by 2 desc;

\echo ''
\echo ' -- jobs reclaimed MORE THAN ONCE in 24h (thrash — a worker that'
\echo ' -- keeps claiming and losing the same job is stuck, not progressing)'
select j.id as job_id, j.status, count(*) as reclaims, max(e.created_at) as last_reclaim
from job_lease_events e join homework_jobs j on j.id = e.job_id
where e.created_at > now() - interval '24 hours'
  and e.event_type in ('reclaimed_stale', 'reclaimed_forced')
group by 1, 2 having count(*) > 1
order by 3 desc limit 20;

\echo ''
\echo '=============================================================='
\echo ' SIGNAL 3 — D1 REGRESSION: done jobs with no Notion outcome.'
\echo ' Invariant: a done job must carry EITHER notion_archived_at (it'
\echo ' pushed) OR notion_skip_reason (it explained why it did not).'
\echo ' Neither = the job finished and vanished from Notion silently.'
\echo ' EXPECT ZERO. Any row here is the D1 bug resurfacing.'
\echo '=============================================================='
select count(*) as d1_violations
from homework_jobs
where status = 'done'
  and notion_archived_at is null
  and notion_skip_reason is null
  and completed_at > now() - interval '7 days';

\echo ''
\echo ' -- the offending jobs (empty is the good result)'
select id as job_id, subject, completed_at, batch_id
from homework_jobs
where status = 'done'
  and notion_archived_at is null
  and notion_skip_reason is null
  and completed_at > now() - interval '7 days'
order by completed_at desc limit 20;

\echo ''
\echo '=============================================================='
\echo ' INVARIANT A — terminal writes must NOT clear claim_token.'
\echo ' If a done/failed job has a NULL token, the heartbeat can read it'
\echo ' as FINISHED->LOST and regress D1. EXPECT ZERO.'
\echo ''
\echo ' SCOPED to jobs actually claimed under the fenced regime (they have'
\echo ' a `claimed` lease event). Without this scope the query counts every'
\echo ' pre-migration-0052 job — they are NULL because the column did not'
\echo ' exist yet, not because anything cleared it. That is a FALSE ALARM,'
\echo ' not a finding: measured 307 such rows on 2026-08-08.'
\echo '=============================================================='
select j.status, count(*) as terminal_jobs_with_null_token
from homework_jobs j
where j.status in ('done', 'failed', 'cancelled')
  and j.claim_token is null
  and exists (select 1 from job_lease_events e
              where e.job_id = j.id and e.event_type = 'claimed')
group by 1 order by 2 desc;

\echo ''
\echo ' -- context for the above: how many jobs have EVER been claimed under'
\echo ' -- the fenced code? If this is 0, #121 has not reached the fleet and'
\echo ' -- every lease signal in this file is vacuously clean.'
select count(distinct job_id) as jobs_ever_claimed_fenced
from job_lease_events where event_type = 'claimed';

\echo ''
\echo '=============================================================='
\echo ' INVARIANT B — a RUNNING job must hold a token and a recent'
\echo ' heartbeat. Token-less running jobs are unfenced; stale-heartbeat'
\echo ' running jobs are reclaim candidates that nothing has reclaimed.'
\echo '=============================================================='
select count(*) filter (where claim_token is null)                      as running_without_token,
       count(*) filter (where claimed_at < now() - interval '2 hours')  as running_claimed_over_2h,
       count(*)                                                          as running_total
from homework_jobs
where status = 'running';

\echo ''
\echo '=============================================================='
\echo ' CONTEXT — is there any traffic at all? A clean board means'
\echo ' nothing if nothing ran. Read every signal above against this.'
\echo '=============================================================='
select count(*) filter (where created_at > now() - interval '24 hours') as lease_events_24h,
       count(*)                                                          as lease_events_total,
       max(created_at)                                                   as newest_event
from job_lease_events;
