import pathlib

_P = pathlib.Path(__file__).resolve().parents[2] / "prompts" / "_general"


def _read(name: str) -> str:
    return (_P / name).read_text(encoding="utf-8")


def test_rlc_not_yet_opener_is_language_relative():
    text = _read("practice-rlc.md")
    low = text.lower()
    assert "output language" in low
    assert "Hali emas" in text            # uz example retained
    assert 'open with **"Hali emas"**' not in text


def test_rlc_red_herring_is_language_relative():
    text = _read("practice-rlc.md")
    low = text.lower()
    assert "output language" in low
    assert "chalg" in low or "отвлека" in low  # uz "chalg'ituvchi" / ru "отвлекающий"


def test_boss_not_yet_opener_is_language_relative():
    text = _read("boss-arena.md")
    low = text.lower()
    assert "output language" in low
    assert "Hali emas" in text  # uz example retained
    assert 'opens with **"Hali emas"**' not in text


def test_cbp_does_not_pre_assert_completion():
    text = _read("case-based-preview.md")
    low = text.lower()
    assert "needs retry" not in low, "CBP still prescribes a decided completion label"
    assert "`passed`" not in text, "CBP still prescribes a decided 'passed' status"
    assert "app" in low, "CBP must state the app owns pass/redo"


def test_cbp_names_two_approved_opening_shapes():
    text = _read("case-based-preview.md")
    low = text.lower()
    assert "storytelling" in low or "story" in low
    assert "question-first" in low or "question first" in low
    assert "fun-fact" in low or "fun fact" in low
