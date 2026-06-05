from app.services.notion_fetch import _map_subject, _first_pdf_block, _url_from_block


def test_map_subject_the_seven():
    assert _map_subject("Algebra") == "math-algebra"
    assert _map_subject("Geometriya") == "geometriya-g7-11"
    assert _map_subject("Fizika") == "physics"
    assert _map_subject("Kimyo") == "kimyo-g7-11"
    assert _map_subject("Biologiya") == "biology"
    assert _map_subject("Ingliz tili") == "english"
    assert _map_subject("Jahon tarixi") == "history"
    assert _map_subject("O‘zbekiston tarixi") == "history"


def test_map_subject_messy_and_unsupported():
    assert _map_subject("Ingliz tili  1-st version missing") == "english"
    assert _map_subject("Matematika\n") is None          # NOT math-algebra
    assert _map_subject("Geografiya") is None
    assert _map_subject("Adabiyot") is None
    assert _map_subject("Tasviriy san’at") is None


def test_first_pdf_block_picks_first_pdf_in_order():
    blocks = [
        {"type": "paragraph"},
        {"type": "file", "file": {"name": "ish_daftari.pdf", "file": {"url": "u-wb"}}},
        {"type": "pdf", "pdf": {"file": {"url": "u-tb"}}},
    ]
    b = _first_pdf_block(blocks)
    assert b is blocks[1]


def test_first_pdf_block_none_when_absent():
    assert _first_pdf_block([{"type": "paragraph"}, {"type": "image"}]) is None


def test_first_pdf_block_skips_non_pdf_file():
    blocks = [
        {"type": "file", "file": {"name": "cover.png", "file": {"url": "u-img"}}},
        {"type": "pdf", "pdf": {"file": {"url": "u-tb"}}},
    ]
    assert _first_pdf_block(blocks) is blocks[1]


def test_url_from_block_shapes():
    assert _url_from_block({"type": "file", "file": {"file": {"url": "A"}}}) == "A"
    assert _url_from_block({"type": "file", "file": {"external": {"url": "B"}}}) == "B"
    assert _url_from_block({"type": "pdf", "pdf": {"file": {"url": "C"}}}) == "C"
    assert _url_from_block({"type": "pdf", "pdf": {"external": {"url": "D"}}}) == "D"
