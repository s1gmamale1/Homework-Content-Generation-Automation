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
