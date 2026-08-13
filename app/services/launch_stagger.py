"""Wave-based batch-launch stagger.

Why this exists (MEASURED 2026-08-11 against production `edu_copy`, batch
`d538c4ef-5347-400f-865a-40a21edbf627` — geografiya g5 RU, 28 lessons,
transport=api):

  * The launcher creates every job in one loop and `scheduled_at` server-defaults
    to NOW(), so all 28 jobs became claimable in the same instant.
  * Every job's first phase (`extract`) is short and TIGHTLY distributed —
    avg 12.6s, p50 13.1s, max 16.1s — so all 28 crossed into their DAG tail
    within ~4s of each other and fanned out together.
  * Measured per-job peak fan-out: 5.54 concurrent api calls (p50 5, max 7).
    28 x 5.54 ~= 155 calls arriving at once against
    CREDENTIAL_MAX_CONCURRENT_GEMINI=32.
  * Result: 16 x "429 fleet credential slot wait exhausted", each burning the
    full 120s budget; 13 of the 16 landed in the first minute.

The decisive counter-evidence for treating this as a VOLUME problem: once the
jobs decorrelated, the same fleet sustained 81 calls in flight with ZERO
exhaustions. Capacity was never the constraint — synchronisation was.

During a burst that outlasts the slot-wait budget the exhaustion count has a
closed form: (model-calling processes x AGENT_MAX_CONCURRENCY) - credential cap.
For that incident (12 x 4) - 32 = 16, matching the 16 observed. Of those three
factors only the burst DURATION is reachable without reconfiguring a frozen
fleet, which is exactly what this module shortens.

This module answers one question and touches nothing else: given a job's 0-based
position in a launch, how many seconds after NOW() may it start?

It owns the OFFSET rule. One derived quantity lives elsewhere by design:
`batch.py::_stagger_summary` turns a launch size back into a wave COUNT for the
API payload. If the offset rule ever changes shape (an exponential ramp, say),
that helper must change with it.
"""


def stagger_offset(index: int, *, wave_size: int, interval_seconds: int) -> int:
    """Seconds after NOW() before the job at 0-based launch ``index`` may start.

    Job ``index`` lands in wave ``index // wave_size`` and starts that many
    intervals from now, so wave 0 is always offset 0. A launch of ``wave_size``
    jobs or fewer therefore behaves EXACTLY as it did before this feature: every
    offset is 0 and every job is claimable immediately.

    Either knob at <= 0 disables staggering (all offsets 0). That is the kill
    switch — `BATCH_LAUNCH_WAVE_SIZE=0` restores pre-plan behaviour with no code
    change (it is read from the settings singleton, so it needs a head restart,
    not a deploy). A single launch can also carry its own ramp without touching
    the head at all: `POST /jobs/batch` accepts `wave_size` /
    `wave_interval_seconds`, which the caller resolves against the settings pair
    before calling this function — either of them at 0 reaches here as 0 and
    lands on the same kill switch.

    KNOWN LIMIT: this shapes only the INITIAL release. Pausing a batch mid-ramp
    and unpausing after the waves have elapsed releases every overdue wave at
    once — the claim gate sees every `scheduled_at` already in the past and the
    pause/unpause endpoints do not re-stagger. A fleet outage spanning the ramp
    does the same. Out of scope here; the herd it rebuilds is bounded by the
    batch's own size, exactly as today.

    ``index`` is the position among the jobs THIS launch actually makes
    claimable (created + resumed) — never the index in the target list. A
    relaunch that adopts or skips 20 of 28 sections adds only 8 jobs of load and
    must not be spread across 5 waves.
    """
    if index <= 0:
        return 0
    if wave_size <= 0 or interval_seconds <= 0:
        return 0
    return (index // wave_size) * interval_seconds
