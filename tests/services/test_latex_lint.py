from app.services.latex_lint import lint_md, lint_phases


def test_contract_v1_commands_pass():
    md = (
        "Savol: $\\frac{1}{S} = \\frac{1}{T_{pl}} - \\frac{1}{T_{\\oplus}}$ va "
        "$\\angle ABC = 90^{\\circ}$, $\\vec{AB}$, $\\sqrt[3]{x}$, "
        "$n \\in \\mathbb{N}$, $a_1 = 4, \\quad d = 3$, "
        "$5 \\text{ yil} \\gt 1$, $x \\ge 0$, $9\\,050\\,300$, $25\\%$.\n"
        "$$T_{pl} = \\frac{S \\cdot T_{\\oplus}}{S + T_{\\oplus}}$$\n"
    )
    assert lint_md("case-based-preview", md) == []


def test_out_of_contract_command_flagged():
    assert any("\\boxed" in v for v in lint_md("flashcards", "$\\boxed{x=5}$"))
    assert any("\\ce" in v for v in lint_md("flashcards", "$\\ce{H2O}$"))


def test_mathbb_argument_restricted():
    assert lint_md("flashcards", "$n \\in \\mathbb{N}$") == []
    assert any("mathbb" in v for v in lint_md("flashcards", "$z \\in \\mathbb{C}$"))


def test_bare_latex_outside_span_flagged():
    out = lint_md("memory-check", "Formula a_n uchun \\frac{1}{2} ni oling.")
    assert any("bare \\frac" in v for v in out)


def test_unbalanced_dollar_flagged():
    out = lint_md("practice-rlc", "Narx $x = 5 bo'lsin.\n")
    assert any("unbalanced $" in v for v in out)


def test_paren_bracket_delimiters_flagged():
    assert any("delimiters" in v for v in lint_md("extract", "\\(x\\) qiymati"))


def test_nested_frac_flagged():
    md = "$\\frac{\\frac{a}{b}}{c}$"
    assert any("nested" in v for v in lint_md("memory-check", md))
    assert lint_md("memory-check", "$\\frac{a}{b} + \\frac{c}{d}$") == []


def test_ed_backticked_variants_must_be_plain():
    good = "## To'g'ri versiya\nQabul qilinadigan variantlar:\n`x = 5`, `x=5`\n"
    assert lint_md("practice-error-detection", good) == []
    bad = "Qabul qilinadigan variantlar:\n`$x = 5$`\n"
    assert any("accepted variant" in v
               for v in lint_md("practice-error-detection", bad))


def test_teacher_pack_fence_requires_doubled_backslashes():
    ok = ('```ELEMENT: test\n{"question": "Formula $\\\\frac{1}{S}$ qaysi?", '
          '"options": ["$\\\\cdot$"]}\n```\n')
    assert lint_md("teacher-pack", ok) == []
    raw = '```ELEMENT: test\n{"question": "Formula $\\frac{1}{S}$ qaysi?"}\n```\n'
    assert any("raw single backslash" in v for v in lint_md("teacher-pack", raw))


def test_teacher_pack_fence_invalid_json_flagged():
    bad = '```ELEMENT: game\n{"items": [}\n```\n'
    assert any("invalid JSON" in v for v in lint_md("teacher-pack", bad))


def test_teacher_pack_canonical_fence_shape_and_prose_scanned():
    md = ('```\nELEMENT: exercise\n{"prompt": "Davri $T_{\\\\oplus} = 1$ yil"}\n```\n'
          "Slayd matni: $\\frac{1}{2}$ va yana matn.\n")
    assert lint_md("teacher-pack", md) == []


def test_lint_phases_aggregates():
    out = lint_phases([
        ("flashcards", "$\\boxed{1}$"),
        ("extract", "toza matn $x^{2}$"),
    ])
    assert len(out) == 1 and out[0].startswith("flashcards")


def test_qa_html_comments_are_exempt():
    md = ("Slayd: $x^{2}$\n"
          "<!-- QA-WHERE: option B quotes \\frac{1}{S}; price tag $15 raw -->\n")
    assert lint_md("teacher-pack", md) == []


def test_notin_is_in_contract():
    assert lint_md("teacher-pack", "$0 \\notin \\mathbb{N}$") == []


def test_blank_inside_math_span_flagged():
    bad = "To'ldiring: $T_{pl} = \\frac{S \\cdot T_{yer}}{S + _____}$"
    assert any("blank inside" in v for v in lint_md("memory-check", bad))
    ok = "To'ldiring: $T_{pl}$ formulasidagi maxraj: _____"
    assert lint_md("memory-check", ok) == []


def test_grade9_math_vocabulary_in_contract():
    md = ("$D(f) = (-\\infty; 0) \\cup (0; +\\infty)$, $y_{\\min}$, "
          "$\\operatorname{tg} x$, $\\tg 45^{\\circ}$, $\\lg 100$, "
          "$A \\cap B = \\emptyset$")
    assert lint_md("extract", md) == []


def test_memory_check_symbolic_fill_blank_rejected():
    md = ("Formulani to'ldiring: $T_{pl}$ uchun maxrajdagi had: _____\n"
          "**Kutilayotgan javob:** T_yer\n"
          "**Muqobil javoblar:** T_Yer, n - 1\n")
    out = lint_md("memory-check", md)
    assert sum("symbolic fill_blank answer" in v for v in out) >= 2


def test_memory_check_word_and_number_answers_pass():
    md = ("Gapni to'ldiring: bu davr _____ deb ataladi.\n"
          "**Kutilayotgan javob:** sinodik davr\n"
          "**Muqobil javoblar:** sinodik, 1/3, 25\n")
    assert lint_md("memory-check", md) == []


def test_memory_check_placeholder_in_span_rejected():
    md = "Ifoda: $T_{pl} = \\frac{S \\cdot T_{yer}}{S + \\text{?}}$ — had: _____\n**Kutilayotgan javob:** yigindi\n"
    assert any("placeholder inside" in v for v in lint_md("memory-check", md))
    # same span content is NOT a memory-check placeholder problem elsewhere
    assert not any("placeholder" in v for v in lint_md("case-based-preview",
        "Savol: $x = \\text{?}$ bo'lsa nima bo'ladi?"))


def test_word_blank_between_two_spans_is_fine():
    md = ("$v = 20$ m/s tezlik va $t = 3$ s vaqt uchun bosib o'tilgan yo'l "
          "_____ formula bilan topiladi.\n**Kutilayotgan javob:** masofa\n")
    assert lint_md("memory-check", md) == []
