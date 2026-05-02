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
    auth_token: str = ""

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
    gemini_max_concurrency: int = 8


settings = Settings()


def valid_auth_tokens() -> set[str]:
    """Parsed valid token set. Empty means auth is disabled."""
    return {t.strip() for t in settings.auth_token.split(",") if t.strip()}
