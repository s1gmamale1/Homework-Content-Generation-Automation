import pytest

from app.services.subjects import history_variant


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("8-sinf Jahon tarixi 2024 (@elekton_darslikbot).pdf", "jahon"),
        ("10 - sinf Jahon Tarixi.pdf", "jahon"),
        ("11-sinf-Jahon-tarixi.pdf", "jahon"),
        ("7-sinf_Ozbekiston_tarixi_2022_(elekton_darslikbot).pdf", "ozbekiston"),
        ("9-sinf O'zbekiston tarixi.pdf", "ozbekiston"),       # U+2019 right quote
        ("8-sinf O‘zbekiston tarixi 2023.pdf", "ozbekiston"),  # U+2018 left quote
        ("10 - sinf Oʻzbekiston tarixi.pdf", "ozbekiston"),    # U+02BB turned comma
        ("5-sinf Tarix (Qadimgi dunyo).pdf", None),            # combined, no split
        ("6-sinf Tarix Qadimgi Dunyo Tarixi.pdf", None),
    ],
)
def test_history_variant_history_subject(filename, expected):
    assert history_variant("history", filename) == expected


def test_history_variant_non_history_subject():
    assert history_variant("math-algebra", "8-sinf Algebra.pdf") is None
    # a non-history subject never splits, even if the filename has a keyword
    assert history_variant("biology", "jahon nimadir.pdf") is None


def test_history_variant_missing_filename():
    assert history_variant("history", None) is None
    assert history_variant("history", "") is None


def test_fold_agrees_with_archive_fold():
    """Drift guard: history_variant must fold apostrophe glyphs the SAME way the
    Notion archive split (notion_archive._fold) does, or the UI label and the
    archive routing would silently disagree. Fails loudly if either fold drops a
    glyph the other keeps."""
    from app.services.notion_archive import _fold as archive_fold

    for glyph in "'‘’ʻ`":
        name = f"7-sinf O{glyph}zbekiston tarixi.pdf"
        assert "ozbekiston" in archive_fold(name)        # archive would route it
        assert history_variant("history", name) == "ozbekiston"  # label agrees
