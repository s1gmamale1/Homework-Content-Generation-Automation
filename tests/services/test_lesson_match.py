from app.services.notion.lesson_match import tokenize, match_lesson, CONTAINER_TITLE


def test_tokenize_strips_markers_leaders_numbers_ellipsis():
    # verbatim history title: U+2026 ellipsis glyphs + trailing page number "6",
    # the "1-mavzu." prefix, and punctuation must all drop.
    t = tokenize("1-mavzu. German qabilalari va Rim imperiyasi…………………6")
    assert t == frozenset({"german", "qabilalari", "va", "rim", "imperiyasi"})


def test_tokenize_strips_ascii_dot_leader():
    # algebra-style ASCII dot leader + page number.
    t = tokenize("1. Sonli ifodalar ....57")
    assert t == frozenset({"sonli", "ifodalar"})


def test_tokenize_folds_apostrophes_not_diacritics():
    # Uzbek apostrophe variants inside words fold away (bo'lim -> bolim); the
    # marker "bo'lim" is then dropped. Diacritics are NOT folded (none here).
    assert tokenize("2-bo'lim Algebraik ifodalar") == frozenset({"algebraik", "ifodalar"})


def test_history_adopts_unique_match():
    human = [{"id": "h1", "title": "1-mavzu. German qabilalari va Rim imperiyasi…………………6"}]
    assert match_lesson("1 German qabilalari va Rim imperiyasi", human) == "h1"


def test_kimyo_adopts_identical_words():
    human = [{"id": "k1", "title": "1-§ Dastlabki kimyoviy tushuncha va qonunlar"}]
    assert match_lesson("1-§ Dastlabki kimyoviy tushuncha va qonunlar", human) == "k1"


def test_algebra_falls_back_different_words():
    human = [{"id": "a1", "title": "1. Yig'indining kvadrati va ayirmaning kvadrati ....57"}]
    assert match_lesson("1 Sonli ifodalar", human) is None


def test_ambiguous_two_supersets_falls_back():
    human = [
        {"id": "p1", "title": "Sulfat kislota"},
        {"id": "p2", "title": "Sulfat kislota xossalari"},
    ]
    assert match_lesson("Sulfat kislota", human) is None


def test_short_title_skips_matching():
    human = [{"id": "h1", "title": "1-mavzu. Kirish darsi"}]
    assert match_lesson("1 Kirish", human) is None  # only {"kirish"} -> < 2 content words


def test_subset_not_equality_still_matches():
    human = [{"id": "h1", "title": "Fotosintez jarayoni va bosqichlari"}]
    assert match_lesson("Fotosintez jarayoni", human) == "h1"


def test_container_page_excluded_from_candidates():
    human = [
        {"id": "c", "title": CONTAINER_TITLE},
        {"id": "h1", "title": "German qabilalari va Rim imperiyasi"},
    ]
    assert match_lesson("German qabilalari va Rim imperiyasi", human) == "h1"


def test_no_human_pages_falls_back():
    assert match_lesson("German qabilalari va Rim imperiyasi", []) is None
