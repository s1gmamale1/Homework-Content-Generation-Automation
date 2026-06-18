from app.config import Settings


def test_extract_robustness_defaults():
    s = Settings(database_url="postgresql+asyncpg://x/y", _env_file=None)
    assert s.extract_max_text_chars > 100_000          # fits a normal textbook's text
    assert s.extract_min_text_chars > 0                 # Gate A floor
    assert 0.0 < s.extract_min_printable_ratio <= 1.0   # Gate A printable-letter ratio
    assert s.extract_min_summary_chars > 0              # Gate B floor
    # Gate B floor must be ABOVE the observed 275-char refusal that motivated this work
    assert s.extract_min_summary_chars >= 400
    # Per-page density floor (sparse/scanned detector)
    assert s.extract_min_chars_per_page == 300


def test_toc_config_knobs():
    s = Settings(database_url="postgresql+asyncpg://x/y", _env_file=None)
    assert s.extract_toc_front_pages == 12
    assert s.extract_toc_back_pages == 20
