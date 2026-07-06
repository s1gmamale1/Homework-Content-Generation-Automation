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

    # Auth: comma-separated list of valid bearer tokens. Empty disables auth
    # (dev/local mode — anyone can call any endpoint). In production, the
    # upstream service injects the token in the request header; for manual
    # frontend access (paste token into login form), the SPA stores it in
    # sessionStorage and attaches it to every API call.
    auth_token: str = "123"

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
    queue_max_attempts: int = 3
    # When `pending` queue depth exceeds this, /generate returns 503. Set
    # to 0 to disable backpressure and accept-all.
    queue_backpressure_limit: int = 50
    # Process-wide cap on simultaneous CLI subprocesses. Protects against
    # rate-limit cascades when multiple workers + parallel scheduler all
    # fan out at once. agent_max_concurrency is the live knob read by
    # agent._effective_concurrency(); gemini_max_concurrency is a DEPRECATED
    # fallback used only when agent_max_concurrency is left at its default (8),
    # so existing .env files that set GEMINI_MAX_CONCURRENCY still work.
    agent_max_concurrency: int = 8  # LIVE knob — set AGENT_MAX_CONCURRENCY to tune
    gemini_max_concurrency: int = 8  # DEPRECATED fallback — honoured only when agent_max_concurrency==8
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
    # card per restart forever. 2h is >> the 90s stale window (no live worker
    # can be pruned) yet keeps the dashboard to recently-live workers only.
    worker_registry_prune_seconds: int = 7200
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

    # ─── Answer-key solver (CQ-C) ──────────────────────────────────────────
    solver_enabled: bool = True
    max_solve_regens: int = 1

    # ─── Reactive rate-limit backoff (concurrency-knob-1, Phase 1) ────────
    # On a transient 429/RESOURCE_EXHAUSTED, agent._spawn retries the SAME call
    # with exponential backoff + jitter instead of failing. Worst-case total
    # wait ≈ 30–56s across retries, well under per_attempt_timeout_seconds=600.
    rate_limit_max_retries: int = 4
    rate_limit_base_delay_seconds: float = 2.0
    rate_limit_max_delay_seconds: float = 30.0

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
    # TOC vision validator: runs after extract_toc, before persisting status.
    # Disabled → toc_validation DB column stays NULL (distinct from "skipped").
    toc_validation_enabled: bool = True
    toc_validation_provider: str = "gemini"
    toc_validation_model: str = "gemini-2.5-flash"

    # ─── Filesystem ───────────────────────────────────────────────────────
    # Where PDFs are persisted on disk.
    var_dir: str = "var"  # relative to project root; PDFs persist at <var_dir>/books/<book_id>/source.pdf

    # Fleet R13 — base URL of the head's API (e.g. "http://192.168.1.69:8000").
    # When a worker is missing a book's source.pdf, it fetches it from here.
    # EMPTY (default) = no fetch: a missing PDF raises as before, so single-box
    # and the head's own embedded worker are unchanged. Set on remote workers.
    fleet_head_url: str = ""

    # ─── Notion archive (Phase 1 push) ───
    notion_enabled: bool = False
    notion_api_key: str = ""
    # Keyed "{subject}|{grade}" → Notion subject-page ID. Parsed from JSON in env.
    # Value is a page-id string, OR a {keyword: page-id} object for grades where
    # one app-subject splits across several Notion pages (e.g. history → Jahon /
    # O‘zbekiston tarixi), matched against the book filename at archive time.
    notion_subject_pages: dict[str, str | dict[str, str]] = Field(default_factory=dict)
    # Root "Lessons" page to crawl for the Fetch-From-Notion browser
    # (grade -> "N - sinf" -> subject pages with attached textbooks).
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

    # ─── Budget monitor (kill-switch) ─────────────────────────────────────
    # Per-batch api spend cap (USD). 0 = disabled (no per-batch pause).
    cost_cap_batch_usd: float = 0.0
    # Rolling 24-hour fleet api spend cap (USD). 0 = disabled (no fleet pause).
    cost_cap_fleet_daily_usd: float = 0.0
    # How often the budget monitor runs (seconds). Mirrors sweep_interval_seconds.
    cost_check_interval_seconds: int = 60


settings = Settings()


def valid_auth_tokens() -> set[str]:
    """Parsed valid token set. Empty means auth is disabled."""
    return {t.strip() for t in settings.auth_token.split(",") if t.strip()}
