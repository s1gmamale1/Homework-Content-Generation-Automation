def test_job_timeout_default_is_1800():
    from app.config import Settings
    assert Settings.model_fields["job_timeout_seconds"].default == 1800


def test_max_file_mb_default_is_textbook_sized():
    # fetch-1: the ingest cap is no longer an LLM limit (every downstream PDF
    # read is bounded to a page window / text budget) — it's purely an
    # ingest/RAM guard. Sized for heavy scanned Uzbek textbooks (the real book
    # that motivated this was 67.5 MB), not the old 50 MB that rejected it.
    # Kept env-overridable via MAX_FILE_MB; 250 keeps per-upload RAM bounded so
    # we don't create a latent OOM (the streaming-ingest rework is the follow-up
    # only a 300 MB+/high-concurrency regime would force).
    from app.config import Settings
    assert Settings.model_fields["max_file_mb"].default == 250


def test_toc_validation_model_default_off_2_5():
    from app.config import settings
    assert settings.toc_validation_model == "gemini-3.5-flash-lite"
