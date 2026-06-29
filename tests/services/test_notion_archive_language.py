"""Language-aware Notion subject-page routing: en/ru content files under its own
Notion page (language-prefixed key); uz stays the bare key (backward compatible);
a missing language page returns None so the caller skips rather than mis-filing
non-Uzbek content into the Uzbek page."""
import app.services.notion_archive as na


def test_uz_uses_bare_key_and_is_the_default():
    mapping = {"geometriya-g7-11|8": "page_uz"}
    # explicit uz and the default arg both hit the bare key — unchanged behavior
    assert na._resolve_subject_page_id(mapping, "geometriya-g7-11", "8", language="uz") == "page_uz"
    assert na._resolve_subject_page_id(mapping, "geometriya-g7-11", "8") == "page_uz"


def test_ru_uses_language_prefixed_key():
    mapping = {"geometriya-g7-11|8": "page_uz", "ru:geometriya-g7-11|8": "page_ru"}
    assert na._resolve_subject_page_id(
        mapping, "geometriya-g7-11", "8", language="ru") == "page_ru"


def test_en_uses_language_prefixed_key():
    mapping = {"geometriya-g7-11|8": "page_uz", "en:geometriya-g7-11|8": "page_en"}
    assert na._resolve_subject_page_id(
        mapping, "geometriya-g7-11", "8", language="en") == "page_en"


def test_non_uz_never_falls_back_to_the_uz_bare_key():
    # only the uz bare key exists — a ru/en job must NOT mis-file into it.
    mapping = {"geometriya-g7-11|8": "page_uz"}
    assert na._resolve_subject_page_id(mapping, "geometriya-g7-11", "8", language="ru") is None
    assert na._resolve_subject_page_id(mapping, "geometriya-g7-11", "8", language="en") is None


def test_language_prefix_composes_with_history_split_dict():
    # the {keyword: page_id} history-split form works under a language prefix too.
    mapping = {"ru:history|8": {"jahon": "ru_jahon", "ozbekiston": "ru_ozbek"}}
    assert na._resolve_subject_page_id(
        mapping, "history", "8", "8-sinf_Jahon_tarixi_2024.pdf", language="ru") == "ru_jahon"
    assert na._resolve_subject_page_id(
        mapping, "history", "8", "8-sinf_Ozbekiston_tarixi.pdf", language="ru") == "ru_ozbek"


def test_no_grade_returns_none_regardless_of_language():
    mapping = {"ru:geometriya-g7-11|8": "page_ru"}
    assert na._resolve_subject_page_id(mapping, "geometriya-g7-11", None, language="ru") is None
