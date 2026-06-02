def test_job_timeout_default_is_1800():
    from app.config import Settings
    assert Settings.model_fields["job_timeout_seconds"].default == 1800
