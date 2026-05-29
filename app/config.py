from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    gemini_api_key: str
    gemini_model: str = "gemini-2.0-flash-exp"
    max_file_mb: int = 50
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
    # Hard ceiling per pipeline. Pipelines exceeding this are aborted and
    # marked failed; a HARD biology run typically takes 60-90s, so 600 is
    # ~10x headroom for really pathological cases.
    job_timeout_seconds: int = 600
    # Max retry attempts before terminal failure. Each Gemini transient
    # error consumes one attempt. After exhaustion the job stays `failed`.
    queue_max_attempts: int = 3
    # When `pending` queue depth exceeds this, /generate returns 503. Set
    # to 0 to disable backpressure and accept-all.
    queue_backpressure_limit: int = 50
    # Process-wide cap on simultaneous Gemini calls. Protects against
    # rate-limit cascades when multiple workers + parallel scheduler all
    # fan out at once.
    agent_max_concurrency: int = 8  # process-wide cap on concurrent CLI subprocesses
    gemini_max_concurrency: int = 8  # DEPRECATED — kept for backwards-compat with agent.py

    # ─── Filesystem ───────────────────────────────────────────────────────
    # Where PDFs are persisted on disk.
    var_dir: str = "var"  # relative to project root; PDFs persist at <var_dir>/books/<book_id>/source.pdf

    # ─── Cheap-extractor pin ──────────────────────────────────────────────
    # Both PDF-reading paths (TOC extraction at upload time + per-section
    # lesson.extract during a job) are pinned to a cheap model regardless of
    # which provider/model the user picked for the rest of the job. Reasoning:
    # extract phases burn the most input tokens (whole-PDF or many-page reads)
    # and the output is a flat factual summary, so paying smart-tier rates
    # buys nothing. Other phases keep using the job-level provider/model.
    extract_provider: str = "gemini"
    extract_model: str = "gemini-2.5-flash"

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


settings = Settings()


def valid_auth_tokens() -> set[str]:
    """Parsed valid token set. Empty means auth is disabled."""
    return {t.strip() for t in settings.auth_token.split(",") if t.strip()}
