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


from unittest.mock import MagicMock
from app.services import notion_fetch as nf


def _client(children_by_parent, blocks_by_page=None):
    c = MagicMock()
    c.get_child_pages.side_effect = lambda pid: children_by_parent.get(pid, [])
    c.get_block_children.side_effect = lambda pid: (blocks_by_page or {}).get(pid, [])
    return c


def test_list_grades_excludes_rules():
    c = _client({"ROOT": [
        {"id": "g9", "title": "9 Grade"}, {"id": "g8", "title": "8 Grade"},
        {"id": "rx", "title": "Rules"},
    ]})
    grades = nf.list_grades(c, "ROOT")
    titles = [g["title"] for g in grades]
    assert "Rules" not in titles and "9 Grade" in titles


def test_list_subjects_sinf_only_with_flags():
    c = _client(
        children_by_parent={
            "g9": [{"id": "uz", "title": "9 - sinf"}, {"id": "ru", "title": "9 - класс"}],
            "uz": [
                {"id": "alg", "title": "Algebra"},
                {"id": "geo", "title": "Geografiya"},
                {"id": "pe", "title": "Jismoniy tarbiya"},
            ],
        },
        blocks_by_page={
            "alg": [{"type": "pdf", "pdf": {"file": {"url": "u"}}}],
            "geo": [{"type": "file", "file": {"name": "x.pdf", "file": {"url": "u"}}}],
            "pe": [{"type": "paragraph"}],
        },
    )
    subs = nf.list_subjects(c, "g9")
    by_title = {s["notion_title"]: s for s in subs}
    assert by_title["Algebra"]["app_subject"] == "math-algebra"
    assert by_title["Algebra"]["has_textbook"] is True
    assert by_title["Geografiya"]["app_subject"] is None
    assert by_title["Geografiya"]["has_textbook"] is True
    assert by_title["Jismoniy tarbiya"]["has_textbook"] is False


def test_list_subjects_no_sinf_child_returns_empty():
    c = _client({"g1": [{"id": "ru", "title": "1 - класс"}]})
    assert nf.list_subjects(c, "g1") == []
