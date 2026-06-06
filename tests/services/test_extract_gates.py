from app.config import settings
from app.services.agent import validate_extract_text, validate_extract_summary


def test_gate_a_accepts_real_text():
    text = "Franklar davlati. " * 200  # long, mostly letters
    assert validate_extract_text(text) is None


def test_gate_a_rejects_empty_and_short():
    assert validate_extract_text("") is not None
    assert validate_extract_text("   \n  ") is not None
    assert validate_extract_text("tiny") is not None


def test_gate_a_rejects_glyph_garbage():
    garbage = "/G55/G6D/G75 " * 400   # /Gxx glyph soup, no real letters (R10 case)
    assert validate_extract_text(garbage) is not None


def test_gate_a_accepts_math_textbook_with_heavy_whitespace():
    # Regression (worklog 0039->0040): a real algebra book extracted with ~35%
    # layout whitespace + digits + symbols scored letters/total = 0.44 and was
    # wrongly terminal-failed. The ratio must be over VISIBLE chars: of the
    # actual glyphs, ~68% are letters here, so it must PASS.
    math_line = "Tenglama   x  +  2  =  5   yechimi   x  =  3 .    "  # spacey, digits, symbols
    text = math_line * 300
    # Sanity: this DOES have heavy whitespace (the old metric would have failed it)
    stripped = text.strip()
    letters = sum(c.isalpha() for c in stripped)
    assert letters / len(stripped) < 0.55, "test fixture must reproduce the old false-reject"
    # The shipped gate (visible-char denominator) must accept it.
    assert validate_extract_text(text) is None


def test_gate_b_accepts_real_summary():
    summary = "# Franklar davlati\n\n" + ("Bu darsda muhim tarixiy voqealar bor. " * 50)
    assert validate_extract_summary(summary) is None


def test_gate_b_rejects_short_refusal():
    # the actual 275-char refusal shape that motivated this work
    refusal = "Dars konteksti mavjud emas — PDF manba fayli ignore sozlamalari tufayli o'qib bo'lmadi."
    assert validate_extract_summary(refusal) is not None


def test_gate_b_does_not_false_trigger_on_legit_uzbek():
    # "mavjud emas" appears INSIDE a long, real summary → must PASS (not a refusal)
    summary = ("# Dars\n\n" + "Tarixiy manbalarga ko'ra ba'zi ma'lumotlar mavjud emas, "
               "ammo asosiy voqealar quyidagicha. " * 40)
    assert validate_extract_summary(summary) is None
