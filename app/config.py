from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into os.environ at import time (a real exported variable always
# wins — override=False). pydantic-settings reads .env for the Settings fields
# below, but the transport=api credentials (ANTHROPIC_API_KEY, GEMINI_API_KEY,
# GOOGLE_APPLICATION_CREDENTIALS, GOOGLE_CLOUD_PROJECT, …) are consumed from
# os.environ directly (worker claim gate + agent._auth_env child envs). Under
# docker compose, `env_file:` already exports .env into the process env; this
# line gives bare-metal (`uv run uvicorn …` / `python -m app.services.worker`)
# the same behavior, so keys in .env work identically everywhere. config.py is
# imported before worker.py computes CAPABILITIES, so the ordering is safe.
load_dotenv(override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    # VESTIGIAL — leftover from the removed google-genai SDK era. The runtime
    # makes NO LLM API/SDK calls; every model call goes through a CLI subprocess
    # (claude/gemini/codex/kimi/opencode), and each CLI uses its own login, not
    # this key. Nothing reads these two fields. Kept (now optional) only so an
    # old .env that still sets GEMINI_API_KEY doesn't error. Safe to delete once
    # no environment references them.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash-exp"
    # Ingest cap for upload + Notion-fetch (MB). NOT an LLM limit — every
    # downstream PDF read is bounded (TOC = 60KB text excerpt or a front/back
    # vision window; lesson-extract = 600K-char text budget or a _subset_pdf
    # page window), so total book size never reaches a model. This cap is purely
    # an ingest/RAM guard. Sized for heavy scanned Uzbek textbooks (fetch-1; the
    # real book that motivated raising it from 50 was 67.5 MB); 250 keeps
    # per-upload RAM bounded (whole body is read + hashed before write), so we
    # don't create a latent OOM. Raise MAX_FILE_MB on the head for bigger books;
    # genuinely huge (300 MB+) ingest would want the streaming-to-disk rework.
    max_file_mb: int = 250
    enable_docs: bool = False
    allow_origins: str = "*"

    # Auth: comma-separated list of valid bearer tokens. Production startup
    # rejects an empty or structurally weak list. Local development must opt in
    # explicitly with ALLOW_INSECURE_LOCAL_AUTH=true and an exactly empty list;
    # that mode never opens the strict service-account-key routes.
    auth_token: str = ""
    allow_insecure_local_auth: bool = False

    # Dashboard viewer auth: a SEPARATE comma-separated token set for the
    # read-only dashboard viewer port (a second FastAPI process). Deliberately
    # distinct from auth_token/AUTH_TOKEN — an operator token must never grant
    # viewer access and vice versa. Empty disables the viewer entirely (see
    # get_viewer_user in app/auth.py, which refuses rather than opening wide).
    dashboard_token: str = ""

    # ─── Queue / worker ───────────────────────────────────────────────────
    # 0 = no in-process worker. >0 = embedded worker runs N concurrent jobs
    # within the API process. For multi-process deployments, set to 0 in the
    # API service and run `python -m app.services.worker` in worker pods.
    worker_concurrency: int = 4
    # Seconds between empty-queue polls. LISTEN/NOTIFY would zero this; for
    # now polling is simple and robust.
    worker_poll_interval: float = 2.0
    # A full Flow v2 claude job (CBP alone is ~274s, plus ~7 more phases) can
    # run well past 600s; the worker kills it and retries to failure. 1800s
    # (30 min) covers a full claude generation with headroom. Cheap models
    # finish far sooner — a provider-aware timeout is a future refinement.
    job_timeout_seconds: int = 1800
    # Max retry attempts before terminal failure. Each Gemini transient
    # error consumes one attempt. After exhaustion the job stays `failed`.
    # This budget bounds *execution* failures only: a claim reclaimed by the
    # stale sweep before the job ever started a phase is refunded and counted
    # against `queue_max_reclaims` instead (retry-accounting-1).
    queue_max_attempts: int = 3
    # Ceiling on CONSECUTIVE never-executed reclaims that get their attempt
    # refunded. Deliberately far above queue_max_attempts: transient lock/DB
    # contention must never destroy queued work, but a job that is reclaimed
    # this many times without EVER starting a phase is genuinely wedged, so the
    # refund stops and the normal `attempts` machinery terminates it. Reset to
    # 0 the moment a claim actually executes.
    queue_max_reclaims: int = 20
    # When `pending` queue depth exceeds this, /generate returns 503. Set
    # to 0 to disable backpressure and accept-all.
    queue_backpressure_limit: int = 50
    # ─── Worker DB connection pool (fleet-wide connection budget) ─────────
    # Per-process SQLAlchemy pool bounds for a WORKER process
    # (WORKER_CONCURRENCY>0). API-only heads keep their own larger request pool
    # — see app/db.py::_pool_config, which also carries the budget arithmetic.
    # The DEFAULTS ARE THE VALUES db.py USED TO HARDCODE (2+2), deliberately: an
    # untouched host behaves exactly as before this knob existed, and a fleet
    # rollout that misses one .env degrades to "unchanged", never to
    # "over-subscribed Postgres".
    # 2+2 is known to throttle, and that is a priced trade: a
    # WORKER_CONCURRENCY=4 host must serve 4 jobs PLUS its heartbeat loop, the
    # credential limiter and the cost monitor out of those 4 connections, so
    # measured 2026-08-12 across 33 hosts holding work only ONE ever reached 4
    # concurrent jobs (17 ran 1, 14 ran 2, 1 ran 3) — the rest surfaced as
    # RETRYABLE `QueuePool limit of size 2 overflow 2 reached ... timeout 30.00`.
    # Raise these ONLY after the upstream ceiling is raised (pgbouncer in front
    # of Postgres, or a larger max_connections): the budget is fleet-wide, and
    # over-raising converts retryable timeouts into hard connection refusals that
    # also lock the head out of its own database.
    # ge=1 / ge=0 because SQLAlchemy reads pool_size=0 and max_overflow=-1 as
    # UNBOUNDED — precisely the fleet-fatal case this budget exists to prevent.
    worker_db_pool_size: int = Field(default=2, ge=1)     # env WORKER_DB_POOL_SIZE
    worker_db_max_overflow: int = Field(default=2, ge=0)  # env WORKER_DB_MAX_OVERFLOW
    # ─── Batch-launch wave stagger (plan 2026-08-11) ──────────────────────
    # A batch launch stamps `scheduled_at` in waves instead of making every job
    # claimable at once. Sized against the MEASURED 2026-08-11 incident (batch
    # d538c4ef, 28 lessons, transport=api): per-job peak api-call fan-out is
    # 5.54 (p50 5, max 7), so 6 jobs per wave puts ~33 calls against
    # CREDENTIAL_MAX_CONCURRENT_GEMINI=32 instead of the ~155 that produced 16
    # slot-wait exhaustions. The interval clears the extract phase (p50 13.1s,
    # max 16.1s) plus one content call (avg 35.9s), so waves cannot stack.
    # Deliberately NOT sized against a raised credential cap: the point is that
    # this works at the fleet's CURRENT configuration with no worker touch.
    # Set either to 0 to disable — every job becomes claimable immediately,
    # exactly as before this feature.
    # These are the fleet-wide DEFAULT, not a ceiling: a bigger fleet would spend
    # ~42 min ramping 254 lessons at 6/60s, and changing these needs a head
    # restart (operationally reserved — it re-stamps the fleet version floor).
    # `POST /jobs/batch` therefore takes optional per-request `wave_size` /
    # `wave_interval_seconds` that override this pair for that launch only.
    batch_launch_wave_size: int = Field(default=6, ge=0)
    batch_launch_wave_interval_seconds: int = Field(default=60, ge=0)
    # Process-wide cap on simultaneous CLI subprocesses. Protects against
    # rate-limit cascades when multiple workers + parallel scheduler all
    # fan out at once. agent_max_concurrency is the live knob read by
    # agent._effective_concurrency(); gemini_max_concurrency is a DEPRECATED
    # fallback used only when agent_max_concurrency is left at its default (8),
    # so existing .env files that set GEMINI_MAX_CONCURRENCY still work.
    # ge=1 on BOTH: `_semaphore()` feeds whichever one wins into
    # `asyncio.Semaphore(n)` (agent.py:249-253), and `asyncio.Semaphore(0)` has
    # ZERO permits — every model call blocks forever with no error and no log,
    # so a host set to 0 claims jobs, makes no calls, looks healthy, and loses
    # each job to `job_timeout_seconds`. Fail at startup instead.
    agent_max_concurrency: int = Field(default=8, ge=1)  # LIVE knob — set AGENT_MAX_CONCURRENCY to tune
    gemini_max_concurrency: int = Field(default=8, ge=1)  # DEPRECATED fallback — honoured only when agent_max_concurrency==8
    # transport=api: claude's Messages API REQUIRES max_tokens (gemini does not,
    # and stays uncapped). 16384 gives headroom over the longest uncapped content
    # phases (reading/preview-hard); hitting it fails LOUD, never silent-truncates.
    api_max_output_tokens: int = 16384  # env API_MAX_OUTPUT_TOKENS

    # ─── Session-limit handling ───────────────────────────────────────────
    # Default IANA timezone when a Claude session-limit message omits a tz in
    # its ``resets <time>`` clause.  The real messages (Oliver log 2026-06-23)
    # always include "(America/Chicago)", but this default makes parse_session_
    # limit_reset deterministic on bare messages that lack the parenthetical.
    session_limit_default_tz: str = "America/Chicago"
    # Fleet-wide default for what the worker does when a Claude session-limit
    # hits during generation.  Per-batch overrides (batches.session_limit_strategy)
    # win when they are an explicit "pause" or "switch"; "inherit" defers here.
    # "pause" = pause the batch and wait for the session to reset (safe default:
    #           preserves the claude allocation; requires a human/scheduler to resume).
    # "switch" = switch to the failover provider and continue (fast but spends the
    #            failover provider's allocation for the remainder of the batch).
    session_limit_strategy: str = "pause"
    # Fallback cooldown duration when a session-limit error gives no reset time.
    # The worker self-cools for this many seconds before resuming claiming.
    session_limit_default_cooldown_seconds: int = 3600

    @field_validator("session_limit_strategy", mode="before")
    @classmethod
    def _validate_session_limit_strategy(cls, v: object) -> object:
        """Reject any value that is not 'pause' or 'switch'.

        Unlike the per-batch column which also allows 'inherit', the env-level
        default must be a concrete action (the resolver falls back here, so
        'inherit' would recurse forever).
        """
        valid = {"pause", "switch"}
        if v not in valid:
            raise ValueError(
                f"SESSION_LIMIT_STRATEGY must be 'pause' or 'switch', got {v!r}"
            )
        return v

    # ─── Resilience: job resume + provider failover ───────────────────────
    # Worker refreshes claimed_at every heartbeat_seconds while a job runs, so a
    # live long job's claim never looks stale. MUST be << reclaim_stale_seconds.
    heartbeat_seconds: int = 30
    # Lease TTL: a `running` job whose claimed_at is older than this is treated
    # as orphaned (dead worker) → reclaimed to `pending`. Safe BELOW job_timeout
    # ONLY because the heartbeat keeps live jobs fresh (spec §3).
    reclaim_stale_seconds: int = 120
    # Fleet registry: a worker upserts its `workers.last_heartbeat` every
    # `heartbeat_seconds` (30s); the head treats it offline if older than this.
    # 90s = 3 missed beats — tolerant of a slow loop without lying about death.
    worker_registry_stale_seconds: int = 90
    # Registry retention: rows whose heartbeat is older than this are DELETED
    # by the periodic worker sweep. pc_id is hostname:pid, so every process
    # restart mints a new row — without pruning the fleet page grows a dead
    # card per restart forever. 10min is >> the 90s stale window (no live
    # worker can be pruned) yet clears a dead host from the dashboard within
    # ~10min of its last heartbeat instead of lingering for hours.
    worker_registry_prune_seconds: int = 600
    # Hard timeout for ONE failover attempt (one provider try), so a hung CLI
    # (e.g. opencode stdin hang) cannot stall a phase until job_timeout. MUST be
    # well above the slowest real phase: CBP alone is ~274s (see job_timeout
    # comment) and run_phase_prompt may internally retry, so 300s would kill a
    # legitimately-slow CBP → asyncio.TimeoutError → misclassified failover off
    # claude. 600s clears that with headroom while still bounding a true hang.
    per_attempt_timeout_seconds: int = 600
    # Fallback provider order for per-phase failover. claude is intentionally
    # ABSENT — reserved for the user's Claude Max allocation (provider isolation).
    failover_provider_order: list[str] = Field(
        default_factory=lambda: ["codex", "gemini", "kimi", "opencode"]
    )

    # ─── Phase validator (LLM judge) ──────────────────────────────────────
    # Judge provider/model now live in the DB (`launch_defaults`), edited at
    # /settings in the UI.  `settings.judge_provider` / `settings.judge_model`
    # have been removed; readers use the DB row (resolved at launch-time into
    # concrete job columns).
    # Maximum regen attempts when a phase fails judge; default 1 = current single-regen behavior.
    max_judge_regens: int = 1
    # Deterministic teacher-pack coverage gate: extra bounded regens when the
    # QA-WHERE citations fail the machine check (0 = gate off).
    teacher_pack_gate_retries: int = 2
    # Teacher-deck illustrations (ELEMENT: image fences filled via the Clodex
    # image model). Fail-open: a host without CLODEX_API_KEY strips the
    # data-less fences and ships the deck imageless with a warning.
    deck_images_enabled: bool = True
    deck_image_model: str = "gpt-image-2"
    deck_image_size: str = "1536x1024"
    deck_image_max: int = 16

    # ─── Answer-key solver (CQ-C) ──────────────────────────────────────────
    solver_enabled: bool = True
    max_solve_regens: int = 1

    # ─── Structured content_json authoring (content-json lane) ─────────────
    # Default False: the pipeline never attempts JSON-authoring for the phases
    # in schemas.content_json.SCHEMAS — every phase renders markdown exactly as
    # before the content_json lane landed. Flip to True (STRUCTURED_OUTPUT_ENABLED=true)
    # to activate structured output once that lane is verified end-to-end. The
    # gate itself lives in pipeline._generate_artifact.
    structured_output_enabled: bool = False

    # ─── Reactive rate-limit backoff (concurrency-knob-1, Phase 1) ────────
    # On a transient 429/RESOURCE_EXHAUSTED, agent._spawn retries the SAME call
    # with exponential backoff + jitter instead of failing. Worst-case total
    # wait ≈ 30–56s across retries, well under per_attempt_timeout_seconds=600.
    rate_limit_max_retries: int = 4
    rate_limit_base_delay_seconds: float = 2.0
    rate_limit_max_delay_seconds: float = 30.0

    # ─── Fleet-wide per-credential api concurrency limiter (BE-16 task 4) ──
    # Provider env defaults consulted by credential_limiter.resolve_limit
    # when no per-key `sa_keys.max_concurrent_calls` override applies (no
    # matching project, or the matching row(s) are all NULL). ge=0 so an
    # operator can explicitly set 0 to fully bypass the cap for a provider
    # (acquire() treats <=0 as its BYPASS sentinel — task 3) without a
    # negative value ever silently doing something undefined (codex-review #8).
    credential_max_concurrent_gemini: int = Field(default=8, ge=0)
    credential_max_concurrent_claude: int = Field(default=8, ge=0)
    credential_max_concurrent_clodex: int = Field(default=8, ge=0)
    # Dedicated slot-wait budget for task 5's wire-point (acquire()'s
    # wait_budget_s). Deliberately far below per_attempt_timeout_seconds
    # (600s): the pipeline's own outer `wait_for` sits at ~that same 600s
    # and would cancel the acquire() wait before the 429-shaped
    # degrade-to-backoff path (consumed by `_spawn`'s existing rate-limit
    # retry loop) ever got a chance to fire. 120s keeps that path reachable
    # (codex-review #1).
    credential_slot_wait_seconds: int = Field(default=120, ge=1)

    # Cooldown for a job parked by fleet credential-slot saturation
    # (queue-correctness-1): status='pending' with scheduled_at pushed this
    # far into the future. Attempt is refunded — saturation is back-pressure,
    # not a job defect.
    slot_saturation_requeue_seconds: int = Field(default=90, ge=1)

    # ─── Extract robustness (local-text + gates) ──────────────────────────
    # Whole-book local text is injected into the extract prompt; if the book's
    # text exceeds this it terminal-fails here by design (large-book generation
    # is the separate subset-TOC/shrink effort). ~600K chars ≈ ~150K tokens —
    # fits a normal <20MB textbook comfortably inside gemini-flash's context.
    extract_max_text_chars: int = 600_000
    extract_window_pages: int = 5        # ± margin around printed page range for scoped extract
    extract_window_max_pages: int = 25   # hard cap on a scoped window (size/cost guard)
    extract_min_chars_per_page: int = 300   # below this avg density → treat PDF as scanned (vision)
    extract_toc_front_pages: int = 12   # vision-TOC: front pages to attach when the text excerpt is too sparse
    extract_toc_back_pages: int = 20    # vision-TOC: back pages (a "Mundarija" often prints at the back; larger margin)
    # Gate A (raw local text): below this many chars, or below this printable-
    # letter ratio, the PDF is treated as unreadable (scanned / broken font).
    extract_min_text_chars: int = 500
    extract_min_printable_ratio: float = 0.55
    # Below this fraction of alphabetic chars belonging to a real alphabet
    # (Latin/Cyrillic/Uzbek), the text layer is garbled (cp1251 mojibake or a
    # subset font whose glyph!=byte) — route to vision. Real books measure
    # >=0.999; the RU-mojibake book scores 0.07. 0.70 leaves a huge margin.
    extract_min_alpha_ratio: float = 0.70
    extract_min_summary_chars: int = 120  # fallback floor when NO contract parses; structural parse is primary
    # Extract-completeness check (warn-only, plan 2026-08-07): one bounded call
    # per FRESH extract comparing the summary against the lesson's own source
    # pages. Advisory only — it never fails a job and never regens.
    # DEFAULT OFF by measurement, not by taste: the 2026-08-07 calibration
    # (docs/research/2026-08-07-extract-coverage-calibration.md) found neither
    # candidate model passed both pre-registered bars — flash-lite missed the
    # hand-verified worked-example case (recall 5/8), and flash caught 8/8 but
    # fired on a known-complete compact extract, flagging items the extract
    # states in prose. Measured cost is also 35x the plan's estimate on the
    # model that works ($0.035/lesson, not $0.001). The code ships complete and
    # tested; an operator enables it deliberately.
    extract_coverage_check_enabled: bool = False
    # Advisory work must not stall the sequential head phase: the check runs
    # OUTSIDE _run_with_failover's per_attempt_timeout_seconds (600s) guard, so
    # it carries its own, much tighter bound.
    extract_coverage_timeout_seconds: int = 120
    # None = inherit the extract role's model (the cheap pinned extractor).
    # Set to a stronger model only if calibration shows the pinned tier can't
    # see the omissions (see docs/research/2026-08-07-extract-coverage-calibration.md).
    extract_coverage_model: str | None = None
    extract_coverage_max_items: int = 8   # cap on items named in one warning
    # TOC vision validator: runs after extract_toc, before persisting status.
    # Disabled → toc_validation DB column stays NULL (distinct from "skipped").
    toc_validation_enabled: bool = True
    toc_validation_provider: str = "gemini"
    toc_validation_model: str = "gemini-3.5-flash"

    # ─── Filesystem ───────────────────────────────────────────────────────
    # Where PDFs are persisted on disk.
    var_dir: str = "var"  # relative to project root; PDFs persist at <var_dir>/books/<book_id>/source.pdf

    # Fleet R13 — base URL of the head's API (e.g. "http://192.168.1.69:8000").
    # When a worker is missing a book's source.pdf, it fetches it from here.
    # EMPTY (default) = no fetch: a missing PDF raises as before, so single-box
    # and the head's own embedded worker are unchanged. Set on remote workers.
    fleet_head_url: str = ""

    # Fleet R13 — the source.pdf fetch gets its OWN wall-clock budget, separate
    # from `job_timeout_seconds`. Measured incident (2026-08-12, book "adabiyot
    # g10", 237.2 MB): the fetch has no total-transfer bound (httpx's timeout is
    # per read, and a trickling stream never trips it), so 24 workers each sat
    # `current_phase IS NULL` for ~15 min and then died on the 1800 s JOB
    # timeout — 35 failed / 16 timed out / 0 lessons. A download that can eat a
    # whole generation budget is a transfer timeout wearing the wrong label.
    # 600 s is deliberately generous: it is a ~400 KB/s floor for that same
    # 237 MB file (a LAN pull of it takes ~25 s), so no healthy fetch notices —
    # but a wedged one now fails at 600 s with a named error instead of
    # occupying a worker slot for the full 1800 s.
    book_fetch_timeout_seconds: int = Field(default=600, ge=1)
    # Loud-warn threshold (MB) for an oversized book source PDF on the fetch
    # path. Every worker missing the book pulls the whole file, so size is the
    # variable that decides whether a launch is survivable — the five healthy
    # books in that incident were 1.7-19.5 MB. Warn only (never refuse): a
    # refusal would make big books unsupported, which is the opposite of the
    # goal. 0 disables the warning.
    book_fetch_warn_mb: int = Field(default=100, ge=0)

    # ─── Notion archive (Phase 1 push) ───
    notion_enabled: bool = False
    notion_api_key: str = ""
    # Keyed "{subject}|{grade}" → Notion subject-page ID. Parsed from JSON in env.
    # Value is a page-id string, OR a {keyword: page-id} object for grades where
    # one app-subject splits across several Notion pages (e.g. history → Jahon /
    # O‘zbekiston tarixi), matched against the book filename at archive time.
    notion_subject_pages: dict[str, str | dict[str, str]] = Field(default_factory=dict)
    # Root "Lessons" page to crawl for the Fetch-From-Notion browser
    # (lessons root -> "N Grade" grade pages -> per-language container child
    # ("N - sinf" uz / "N - класс" ru / "N - english" en) -> subject pages
    # with attached textbooks).
    notion_lessons_root: str = "2c1998381c768063bc43c84d59c0abf3"

    # ─── Per-provider call-count caps, per rolling window ─────────────────
    # 0 = unmetered (the /usage page renders a `—` instead of a percentage).
    # The four CLIs (claude, kimi, codex, gemini) don't expose real quota
    # APIs in headless mode, so the dashboard tracks LOCAL consumption —
    # calls THIS app has issued — within fixed rolling windows. Match these
    # values to the plan/tier you're on for each provider so the
    # percentages reflect real headroom.
    agent_limit_claude_1h: int = 100
    agent_limit_claude_24h: int = 1000
    agent_limit_claude_7d: int = 5000

    agent_limit_kimi_1h: int = 60
    agent_limit_kimi_24h: int = 600
    agent_limit_kimi_7d: int = 3000

    agent_limit_codex_1h: int = 60
    agent_limit_codex_24h: int = 500
    agent_limit_codex_7d: int = 2500

    agent_limit_gemini_1h: int = 60
    agent_limit_gemini_24h: int = 1500
    agent_limit_gemini_7d: int = 10000

    agent_limit_clodex_1h: int = 60
    agent_limit_clodex_24h: int = 500
    agent_limit_clodex_7d: int = 2500

    # ─── Budget monitor (kill-switch) ─────────────────────────────────────
    # Per-batch api spend cap (USD). 0 = disabled (no per-batch pause).
    cost_cap_batch_usd: float = 0.0
    # Rolling 24-hour fleet api spend cap (USD). 0 = disabled (no fleet pause).
    cost_cap_fleet_daily_usd: float = 0.0
    # How often the budget monitor runs (seconds). Mirrors sweep_interval_seconds.
    cost_check_interval_seconds: int = 60


settings = Settings()


def valid_auth_tokens() -> set[str]:
    """Request-time token set; startup policy decides whether empty is safe."""
    return {t.strip() for t in settings.auth_token.split(",") if t.strip()}


def valid_dashboard_tokens() -> set[str]:
    """Parsed valid dashboard-viewer token set. Empty means the viewer is
    unconfigured (get_viewer_user refuses rather than opening wide)."""
    return {t.strip() for t in settings.dashboard_token.split(",") if t.strip()}
