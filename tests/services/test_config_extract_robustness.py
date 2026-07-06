from app.config import Settings


def test_extract_robustness_defaults():
    s = Settings(database_url="postgresql+asyncpg://x/y", _env_file=None)
    assert s.extract_max_text_chars > 100_000          # fits a normal textbook's text
    assert s.extract_min_text_chars > 0                 # Gate A floor
    assert 0.0 < s.extract_min_printable_ratio <= 1.0   # Gate A printable-letter ratio
    assert s.extract_min_summary_chars > 0              # Gate B fallback floor
    # Gate B is now structure-first (coverage-contract lane): a parseable
    # enumerated contract passes regardless of length, so this knob is only the
    # FALLBACK floor for output with no contract sections (near-empty /
    # unformatted refusals). Lowered 400->120; the old 275-char refusal is now
    # caught by refusal markers + the structural check, not this floor.
    assert s.extract_min_summary_chars == 120
    # Per-page density floor (sparse/scanned detector)
    assert s.extract_min_chars_per_page == 300


def test_toc_config_knobs():
    s = Settings(database_url="postgresql+asyncpg://x/y", _env_file=None)
    assert s.extract_toc_front_pages == 12
    assert s.extract_toc_back_pages == 20
