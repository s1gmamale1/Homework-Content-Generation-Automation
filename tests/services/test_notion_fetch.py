import pytest

from app.services.notion_fetch import (
    _map_subject, _select_candidate, _url_from_block, _pdf_rank, _fold,
    _grade_number_from_title, _PART_TITLE_RE,
)


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
    # All-subjects registry: these are now first-class (was None pre-registry).
    assert _map_subject("Matematika\n") == "matematika"   # its own code, NOT algebra
    assert _map_subject("Geografiya") == "geografiya"
    assert _map_subject("Adabiyot") == "adabiyot"
    # Textbook-bearing non-exam subject — kept.
    assert _map_subject("Tasviriy san’at") == "tasviriy-sanat"
    # PE is excluded by decision (must NOT mis-map to Upbringing via "tarbiya").
    assert _map_subject("Jismoniy tarbiya") is None
    # A genuinely non-curriculum title still maps to None.
    assert _map_subject("Sinf rahbari soati") is None


def test_grade_number_from_title_int_normalizes_zero_padding():
    # Review fix (task 4): must int()->str() normalize like the sibling
    # derive_grade_from_filename (app/services/grade.py) so a zero-padded
    # "09-sinf" title still matches grade "9", not the literal string "09".
    assert _grade_number_from_title("09-sinf") == "9"
    assert _grade_number_from_title("9-sinf") == "9"
    assert _grade_number_from_title("9 - sinf (yangi)") == "9"
    assert _grade_number_from_title("not a grade title") is None


# ---------------------------------------------------------------------------
# _select_candidate — resolves which candidate `download_textbook` fetches,
# given the page's already-enumerated `textbook_candidates(...)` list. This
# replaces the old `_first_pdf_block` (deleted BE-19 task 3 — it operated on
# raw blocks directly and silently broke rank ties by page order; the
# candidate-based selector below REJECTS ties via `AmbiguousTextbook` instead).
# ---------------------------------------------------------------------------


def _cand(block_id: str, filename: str, rank: int, page_id: str = "p1") -> dict:
    return {"page_id": page_id, "block_id": block_id, "filename": filename,
            "rank": rank, "url": f"u-{block_id}"}


def test_select_candidate_prefers_best_rank_tier():
    candidates = [
        _cand("wb", "ish_daftari.pdf", 2),
        _cand("tb", "8-sinf_algebra_darslik.pdf", 0),
    ]
    assert _select_candidate(candidates)["block_id"] == "tb"


def test_select_candidate_prefers_best_rank_tier_regardless_of_order():
    candidates = [
        _cand("tb", "8-sinf_algebra_darslik.pdf", 0),
        _cand("wb", "ish_daftari.pdf", 2),
    ]
    assert _select_candidate(candidates)["block_id"] == "tb"


def test_select_candidate_single_candidate_returned_even_if_workbook_rank():
    candidates = [_cand("wb", "ish_daftari.pdf", 2)]
    assert _select_candidate(candidates)["block_id"] == "wb"


def test_pdf_rank_bot_handle_with_darslik_is_not_textbook():
    # Live bug (gatekeeper-verified): the Telegram bot handle
    # "@elektron_darslikbot" contains "darslik", so a workbook filename that
    # merely names its download source via that handle used to be misread as
    # a textbook (rank 0). The handle must be stripped before marker matching.
    name = _fold("mashq daftari (@elektron_darslikbot).pdf")
    assert _pdf_rank(name) == 2


def test_pdf_rank_plain_darslik_still_textbook():
    assert _pdf_rank(_fold("6_sinf_matematika_darslik_2024.pdf")) == 0


def test_pdf_rank_plain_workbook_unchanged():
    assert _pdf_rank(_fold("ish daftari.pdf")) == 2


def test_pdf_rank_ru_textbook_marker():
    assert _pdf_rank(_fold("учебник 5-класс.pdf")) == 0


def test_pdf_rank_ru_workbook_marker_full_phrase():
    assert _pdf_rank(_fold("рабочая тетрадь 5-класс.pdf")) == 2


def test_pdf_rank_ru_workbook_marker_bare_tetrad():
    assert _pdf_rank(_fold("тетрадь.pdf")) == 2


def test_pdf_rank_bot_handle_suffixed_textbook_still_textbook():
    # The handle-strip must not eat the genuine "darslik" marker that precedes
    # a trailing source handle on an actual textbook filename.
    name = _fold("8-sinf algebra darslik (@elektron_darslikbot).pdf")
    assert _pdf_rank(name) == 0


def test_fold_lowercases_cyrillic_for_rank():
    # Capitalized Cyrillic input confirms _fold's .lower() case-folds Cyrillic
    # the same way it lower-cases Latin, so the RU markers match post-fold —
    # this is what lets textbook_candidates rank a Cyrillic-titled PDF right.
    assert _pdf_rank(_fold("Учебник 5-класс.pdf")) == 0
    assert _pdf_rank(_fold("Рабочая тетрадь 5-класс.pdf")) == 2


def test_select_candidate_ties_in_best_tier_raise_ambiguous():
    # The key behavior change (BE-19 task 3): the old _first_pdf_block silently
    # broke same-rank ties by page order (block-id-blind picking "part 1" for a
    # multi-part textbook). The candidate-based selector now REFUSES instead.
    candidates = [
        _cand("first", "9-sinf algebra 1-qism.pdf", 0),
        _cand("second", "9-sinf algebra 2-qism.pdf", 0),
    ]
    with pytest.raises(nf.AmbiguousTextbook) as exc_info:
        _select_candidate(candidates)
    assert exc_info.value.candidates == candidates


def test_select_candidate_explicit_block_id_returns_exact_match():
    # An explicit block_id wins even over rank — the caller has already decided.
    candidates = [
        _cand("tb", "darslik.pdf", 0),
        _cand("wb", "ish_daftari.pdf", 2),
    ]
    assert _select_candidate(candidates, block_id="wb")["block_id"] == "wb"


def test_select_candidate_explicit_block_id_not_among_candidates_raises():
    # StaleSelector is a NoTextbook subclass (review fix): the message must be
    # distinct/actionable — name the offending block_id and the candidate
    # count — so the route can tell a stale selector apart from a truly empty
    # page instead of emitting the same generic "no textbook" text for both.
    candidates = [_cand("tb", "darslik.pdf", 0)]
    with pytest.raises(nf.NoTextbook):
        _select_candidate(candidates, block_id="does-not-exist")
    with pytest.raises(nf.StaleSelector) as exc_info:
        _select_candidate(candidates, block_id="does-not-exist")
    msg = str(exc_info.value)
    assert "does-not-exist" in msg
    assert "1" in msg  # candidate count
    assert "stale selector" in msg.lower()


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
            "alg": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "u"}}}],
            "geo": [{"id": "b2", "type": "file", "file": {"name": "x.pdf", "file": {"url": "u"}}}],
            "pe": [{"id": "b3", "type": "paragraph"}],
        },
    )
    subs = nf.list_subjects(c, "g9")
    by_title = {s["notion_title"]: s for s in subs}
    assert by_title["Algebra"]["app_subject"] == "math-algebra"
    assert by_title["Algebra"]["has_textbook"] is True
    assert by_title["Geografiya"]["app_subject"] == "geografiya"
    assert by_title["Geografiya"]["has_textbook"] is True
    assert by_title["Jismoniy tarbiya"]["has_textbook"] is False


def test_list_subjects_no_sinf_child_returns_empty():
    c = _client({"g1": [{"id": "ru", "title": "1 - класс"}]})
    assert nf.list_subjects(c, "g1") == []


from app.services.notion_fetch import download_textbook, TextbookTooLarge, NoTextbook, AmbiguousTextbook


def test_download_rejects_when_no_pdf_block():
    c = _client({}, blocks_by_page={"sub": [{"type": "paragraph"}]})
    with pytest.raises(NoTextbook):
        download_textbook(c, "sub")


def _stub_http(stream_obj):
    """Build a stubbed httpx.Client whose .stream(...) yields stream_obj."""
    class _HTTP:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def stream(self, method, url, follow_redirects=True): return stream_obj
    return lambda **k: _HTTP()


def test_download_rejects_oversize_via_content_length(monkeypatch):
    # Notion S3 URLs are presigned for GET only (HEAD 403s), so the size check
    # reads Content-Length off a streaming GET and rejects BEFORE reading the body.
    # Oversize is relative to the upload cap (settings.max_file_mb), not a
    # hardcoded 20 MB.
    from app.config import settings
    c = _client({}, blocks_by_page={"sub": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "http://x/b.pdf"}}}]})
    oversize = (settings.max_file_mb + 5) * 1024 * 1024

    class _Stream:
        headers = {"Content-Length": str(oversize)}
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self): pass
        def read(self): raise AssertionError("must reject oversize before reading the body")

    monkeypatch.setattr("app.services.notion_fetch.httpx.Client", _stub_http(_Stream()))
    with pytest.raises(TextbookTooLarge):
        download_textbook(c, "sub")


def test_download_accepts_above_old_20mb_cap(monkeypatch):
    # Fetch ceiling raised 20 MB -> the upload cap (settings.max_file_mb) and tied
    # so they can't drift. A 30 MB book (rejected under the old cap) now passes.
    from app.config import settings
    assert settings.max_file_mb >= 50
    c = _client({}, blocks_by_page={"sub": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "http://x/b.pdf"}}}]})

    class _Stream:
        headers = {"Content-Length": str(30 * 1024 * 1024)}
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self): pass
        def read(self): return b"%PDF-1.4 small body"

    monkeypatch.setattr("app.services.notion_fetch.httpx.Client", _stub_http(_Stream()))
    body, filename = download_textbook(c, "sub")
    assert body == b"%PDF-1.4 small body"
    assert filename == "textbook.pdf"


def test_download_returns_bytes_via_streaming_get(monkeypatch):
    c = _client({}, blocks_by_page={
        "sub": [{"id": "b1", "type": "file", "file": {"name": "tb.pdf", "file": {"url": "http://x/b.pdf"}}}]})

    class _Stream:
        headers = {"Content-Length": "9"}
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self): pass
        def read(self): return b"%PDF-1.4 "

    monkeypatch.setattr("app.services.notion_fetch.httpx.Client", _stub_http(_Stream()))
    body, filename = download_textbook(c, "sub")
    assert body == b"%PDF-1.4 "
    assert filename == "tb.pdf"


# ---------------------------------------------------------------------------
# download_textbook selection semantics (BE-19 task 3): candidate-based, with
# an explicit block_id selector, and ambiguity REJECTED instead of a silent
# first-pick.
# ---------------------------------------------------------------------------


def _stub_download(monkeypatch, body: bytes = b"%PDF-1.4 x"):
    class _Stream:
        headers = {"Content-Length": str(len(body))}
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self): pass
        def read(self): return body
    monkeypatch.setattr("app.services.notion_fetch.httpx.Client", _stub_http(_Stream()))


def test_download_single_textbook_with_workbook_also_attached_needs_no_selector(monkeypatch):
    # G1-UZ Texnologiya real shape: one textbook + one workbook attached to the
    # SAME page, different rank tiers -> no ambiguity, no block_id needed. This
    # is the most common real page shape and must keep working without a
    # selector after ambiguity-rejection ships.
    _stub_download(monkeypatch)
    c = _client({}, blocks_by_page={"sub": [
        {"id": "wb", "type": "file", "file": {"name": "ish_daftari.pdf", "file": {"url": "u-wb"}}},
        {"id": "tb", "type": "file", "file": {"name": "8-sinf_texnologiya_darslik.pdf", "file": {"url": "u-tb"}}},
    ]})
    body, filename = download_textbook(c, "sub")
    assert filename == "8-sinf_texnologiya_darslik.pdf"


def test_download_multipart_same_rank_page_without_block_id_raises_ambiguous(monkeypatch):
    # CRITICAL back-compat change: G11-UZ Algebra style page with TWO same-rank
    # parts. Before this task this silently downloaded "part 1"; now it must
    # 422 (AmbiguousTextbook) until the caller passes block_id.
    _stub_download(monkeypatch)
    c = _client({}, blocks_by_page={"sub": [
        {"id": "p1", "type": "file", "file": {"name": "algebra 1-qism.pdf", "file": {"url": "u1"}}},
        {"id": "p2", "type": "file", "file": {"name": "algebra 2-qism.pdf", "file": {"url": "u2"}}},
    ]})
    with pytest.raises(AmbiguousTextbook) as exc_info:
        download_textbook(c, "sub")
    block_ids = {c["block_id"] for c in exc_info.value.candidates}
    assert block_ids == {"p1", "p2"}


def test_download_explicit_block_id_downloads_exact_candidate(monkeypatch):
    _stub_download(monkeypatch, body=b"%PDF-1.4 part2")
    c = _client({}, blocks_by_page={"sub": [
        {"id": "p1", "type": "file", "file": {"name": "algebra 1-qism.pdf", "file": {"url": "u1"}}},
        {"id": "p2", "type": "file", "file": {"name": "algebra 2-qism.pdf", "file": {"url": "u2"}}},
    ]})
    body, filename = download_textbook(c, "sub", block_id="p2")
    assert filename == "algebra 2-qism.pdf"
    assert body == b"%PDF-1.4 part2"


def test_download_explicit_block_id_reaches_a_child_page_candidate(monkeypatch):
    # block_id selection must work for a candidate living on a child_page, not
    # just direct blocks (textbook_candidates records the CHILD page_id there,
    # but the block_id is still enough to select it from the parent's call).
    _stub_download(monkeypatch, body=b"%PDF-1.4 childpart")
    c = _client({}, blocks_by_page={
        "parent": [{"id": "cp1", "type": "child_page", "child_page": {"title": "1-qism"}}],
        "cp1": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "u1"}}}],
    })
    body, filename = download_textbook(c, "parent", block_id="b1")
    assert body == b"%PDF-1.4 childpart"


def test_download_explicit_block_id_not_among_candidates_422s_as_notextbook(monkeypatch):
    _stub_download(monkeypatch)
    c = _client({}, blocks_by_page={"sub": [
        {"id": "tb", "type": "file", "file": {"name": "darslik.pdf", "file": {"url": "u-tb"}}},
    ]})
    with pytest.raises(NoTextbook):
        download_textbook(c, "sub", block_id="does-not-exist")
    with pytest.raises(nf.StaleSelector) as exc_info:
        download_textbook(c, "sub", block_id="does-not-exist")
    assert "does-not-exist" in str(exc_info.value)


# ---------------------------------------------------------------------------
# textbook_candidates — enumerate every PDF reachable from a page: direct
# blocks, containers (toggle/column_list/column, depth-bound), and one level
# of child_page (BE-19 task 2).
# ---------------------------------------------------------------------------


def test_textbook_candidates_two_direct_pdfs_both_found():
    c = _client({}, blocks_by_page={
        "page1": [
            {"id": "b1", "type": "pdf", "pdf": {"file": {"url": "u1"}}},
            {"id": "b2", "type": "file", "file": {"name": "tb.pdf", "file": {"url": "u2"}}},
        ],
    })
    cands = nf.textbook_candidates(c, "page1")
    assert len(cands) == 2
    assert {cd["block_id"] for cd in cands} == {"b1", "b2"}
    assert all(cd["page_id"] == "page1" for cd in cands)
    # block order preserved
    assert [cd["block_id"] for cd in cands] == ["b1", "b2"]


def test_textbook_candidates_pdf_inside_toggle_is_found():
    c = _client({}, blocks_by_page={
        "page1": [{"id": "tg1", "type": "toggle", "toggle": {}}],
        "tg1": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "u1"}}}],
    })
    cands = nf.textbook_candidates(c, "page1")
    assert len(cands) == 1
    assert cands[0]["block_id"] == "b1"
    # the toggle isn't a page: the candidate's page_id is the PARENT page
    assert cands[0]["page_id"] == "page1"


def test_textbook_candidates_pdf_inside_nested_column_is_found():
    c = _client({}, blocks_by_page={
        "page1": [{"id": "cl1", "type": "column_list", "column_list": {}}],
        "cl1": [{"id": "col1", "type": "column", "column": {}}],
        "col1": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "u1"}}}],
    })
    cands = nf.textbook_candidates(c, "page1")
    assert len(cands) == 1
    assert cands[0]["block_id"] == "b1"
    assert cands[0]["page_id"] == "page1"


def test_textbook_candidates_child_pages_carry_child_page_id():
    c = _client({}, blocks_by_page={
        "parent": [
            {"id": "cp1", "type": "child_page", "child_page": {"title": "1-qism"}},
            {"id": "cp2", "type": "child_page", "child_page": {"title": "2-qism"}},
        ],
        "cp1": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "u1"}}}],
        "cp2": [{"id": "b2", "type": "pdf", "pdf": {"file": {"url": "u2"}}}],
    })
    cands = nf.textbook_candidates(c, "parent")
    assert len(cands) == 2
    by_block = {cd["block_id"]: cd for cd in cands}
    assert by_block["b1"]["page_id"] == "cp1"
    assert by_block["b2"]["page_id"] == "cp2"


def test_textbook_candidates_grandchild_pages_not_visited():
    # child_page descent is bounded to ONE level: a child_page nested inside
    # another child_page's blocks must NOT be visited (no grandchildren).
    c = _client({}, blocks_by_page={
        "parent": [{"id": "cp1", "type": "child_page", "child_page": {"title": "1-qism"}}],
        "cp1": [{"id": "gcp1", "type": "child_page", "child_page": {"title": "nested"}}],
        "gcp1": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "u1"}}}],
    })
    cands = nf.textbook_candidates(c, "parent")
    assert cands == []


# ---------------------------------------------------------------------------
# child_page descent is filtered by title (BE-19 live-acceptance perf fix):
# live subject pages carry the generated-homework archive as dozens-hundreds
# of child pages alongside the real book-part pages, so unfiltered descent
# cost ~2,000 rate-limited API calls / grade (720s measured). Only child pages
# whose title looks like a book part/section, or names the textbook itself,
# get descended into.
# ---------------------------------------------------------------------------


def test_part_title_re_matches_recognized_part_and_textbook_titles():
    for title in [
        "Matematika 1-qism", "Algebra 2-qism", "Часть-1", "часть 2",
        "Part 1", "part-2", "Bo'lim 1", "1-kitob", "Algebra darslik",
        "Textbook", "Учебник", "9-sinf algebra 1-qism",
    ]:
        assert _PART_TITLE_RE.search(_fold(title)), title


def test_part_title_re_rejects_homework_archive_titles():
    for title in [
        "19-§ Burchakning sinusi, kosinusi va tangensi",
        "12-§ Kvadrat tenglamalar",
        "Generated Homeworks",
        "2026-07-15",
        "Mavzu 3",
    ]:
        assert not _PART_TITLE_RE.search(_fold(title)), title


def test_textbook_candidates_skips_non_part_homework_child_pages():
    # Adversarial: the homework child page ALSO has a PDF block nested under
    # it (e.g. an attached worksheet) -- it must still be excluded, and its
    # children must NEVER be fetched at all (the point is the API-call
    # saving, not just filtering the returned candidates).
    c = _client({}, blocks_by_page={
        "subj": [
            {"id": "cp1", "type": "child_page", "child_page": {"title": "Matematika 1-qism"}},
            {"id": "cp2", "type": "child_page", "child_page": {"title": "Часть-2"}},
            {"id": "hw1", "type": "child_page", "child_page": {"title": "12-§ Kvadrat tenglamalar"}},
        ],
        "cp1": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "u1"}}}],
        "cp2": [{"id": "b2", "type": "pdf", "pdf": {"file": {"url": "u2"}}}],
        "hw1": [{"id": "b3", "type": "pdf", "pdf": {"file": {"url": "u3"}}}],
    })
    cands = nf.textbook_candidates(c, "subj")
    assert {cd["block_id"] for cd in cands} == {"b1", "b2"}
    # key assertion: hw1's children were never fetched at all
    fetched_containers = {call.args[0] for call in c.get_block_children.call_args_list}
    assert "hw1" not in fetched_containers
    assert {"subj", "cp1", "cp2"} <= fetched_containers


def test_textbook_candidates_rank_attached_per_candidate():
    c = _client({}, blocks_by_page={
        "page1": [
            {"id": "b1", "type": "file", "file": {"name": "ish daftari.pdf", "file": {"url": "u1"}}},
            {"id": "b2", "type": "file", "file": {"name": "algebra darslik.pdf", "file": {"url": "u2"}}},
        ],
    })
    cands = nf.textbook_candidates(c, "page1")
    by_block = {cd["block_id"]: cd for cd in cands}
    assert by_block["b1"]["rank"] == 2  # workbook
    assert by_block["b2"]["rank"] == 0  # textbook


def test_textbook_candidates_url_present_via_url_from_block():
    c = _client({}, blocks_by_page={
        "page1": [{"id": "b1", "type": "pdf", "pdf": {"external": {"url": "http://ext/u.pdf"}}}],
    })
    cands = nf.textbook_candidates(c, "page1")
    assert cands[0]["url"] == "http://ext/u.pdf"


def test_textbook_candidates_ignores_non_pdf_blocks():
    c = _client({}, blocks_by_page={
        "page1": [
            {"id": "p1", "type": "paragraph"},
            {"id": "img1", "type": "file", "file": {"name": "cover.png", "file": {"url": "u"}}},
        ],
    })
    assert nf.textbook_candidates(c, "page1") == []


def test_textbook_candidates_deeply_nested_containers_beyond_bound_not_found():
    # ~3 container levels are traversed; a PDF nested a 4th level deep inside
    # toggles-within-toggles must NOT be found (depth-bound).
    c = _client({}, blocks_by_page={
        "page1": [{"id": "t1", "type": "toggle", "toggle": {}}],
        "t1": [{"id": "t2", "type": "toggle", "toggle": {}}],
        "t2": [{"id": "t3", "type": "toggle", "toggle": {}}],
        "t3": [{"id": "t4", "type": "toggle", "toggle": {}}],
        "t4": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "u1"}}}],
    })
    assert nf.textbook_candidates(c, "page1") == []


def test_subjects_under_finds_textbook_only_in_child_pages():
    # G1-UZ Matematika shape: the subject page itself has no direct PDF, but
    # its two child "qism" pages each hold one. has_textbook must flip True
    # and the candidates list must carry both.
    c = _client(
        children_by_parent={
            "g1": [{"id": "uz", "title": "1 - sinf"}],
            "uz": [{"id": "math", "title": "Matematika"}],
        },
        blocks_by_page={
            "math": [
                {"id": "cpb1", "type": "child_page", "child_page": {"title": "1-qism"}},
                {"id": "cpb2", "type": "child_page", "child_page": {"title": "2-qism"}},
            ],
            "cpb1": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "u1"}}}],
            "cpb2": [{"id": "b2", "type": "pdf", "pdf": {"file": {"url": "u2"}}}],
        },
    )
    subs = nf.list_subjects(c, "g1")
    by_title = {s["notion_title"]: s for s in subs}
    assert by_title["Matematika"]["has_textbook"] is True
    assert len(by_title["Matematika"]["candidates"]) == 2
    assert {cd["page_id"] for cd in by_title["Matematika"]["candidates"]} == {"cpb1", "cpb2"}


def test_available_languages_has_textbook_true_when_only_in_child_pages():
    c = _client(
        children_by_parent={
            "g1": [{"id": "uz", "title": "1 - sinf"}],
            "uz": [{"id": "math", "title": "Matematika"}],
        },
        blocks_by_page={
            "math": [
                {"id": "cpb1", "type": "child_page", "child_page": {"title": "1-qism"}},
            ],
            "cpb1": [{"id": "b1", "type": "pdf", "pdf": {"file": {"url": "u1"}}}],
        },
    )
    result = nf.available_languages(c, "g1")
    assert "matematika" in result
    uz_entry = result["matematika"]["uz"]
    assert uz_entry["has_textbook"] is True
    part = uz_entry["parts"][0]
    assert part["page_id"] == "math"
    assert part["candidates"][0]["page_id"] == "cpb1"
