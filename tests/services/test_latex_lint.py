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


def test_ed_backticked_variants_keyboard_form():
    good = "## To'g'ri versiya\nQabul qilinadigan variantlar:\n`x = 5`, `x=5`\n"
    assert lint_md("practice-error-detection", good) == []
    latex = ("Qabul qilinadigan variantlar:\n"
             "`\\sin(\\alpha) = -\\frac{3}{5}`, `y = 5t^2`\n")
    assert lint_md("practice-error-detection", latex) == []
    bad = "Qabul qilinadigan variantlar:\n`$x = 5$`\n"
    assert any("typed answer key" in v
               for v in lint_md("practice-error-detection", bad))
    paren = "Qabul qilinadigan variantlar:\n`x = 64^(2/3)`\n"
    assert any("parenthesised script" in v
               for v in lint_md("practice-error-detection", paren))
    bare = "Qabul qilinadigan variantlar:\n`sin(a) = -3/5`\n"
    assert any("bare function name" in v
               for v in lint_md("practice-error-detection", bare))
    op = "Qabul qilinadigan variantlar:\n`\\operatorname{tg}\\alpha`\n"
    assert any("operatorname" in v
               for v in lint_md("practice-error-detection", op))


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

def test_typed_element_answers_keyboard_form():
    bad = ('```ELEMENT: test\n{"type": "short_text", "question": "Q $x$?", '
           '"correct_answers": ["x = 64^(2/3)"]}\n```\n')
    assert any("parenthesised script" in v for v in lint_md("teacher-pack", bad))
    good = ('```ELEMENT: test\n{"type": "short_text", "question": "Q $x$?", '
            '"correct_answers": ["x = 64^{\\\\frac{2}{3}}"]}\n```\n')
    assert lint_md("teacher-pack", good) == []


def test_tap_select_answers_exempt_from_keyboard_form():
    md = ('```ELEMENT: test\n{"type": "single_choice", "question": "Q?", '
          '"options": ["$x^{2}$", "$x^{3}$"], '
          '"correct_answers": ["$x^{2}$"]}\n```\n')
    assert lint_md("teacher-pack", md) == []


def test_answer_key_directive_fields_checked():
    md = ('```ELEMENT: exercise\n{"prompt": "p", '
          '"answer_spec": {"expected": "sin(alpha) = -3/5"}}\n```\n')
    out = lint_md("teacher-pack", md)
    assert any("bare function name" in v for v in out)
    assert any("bare greek word" in v for v in out)


def test_unbalanced_answer_key_flagged():
    md = ('```ELEMENT: test\n{"type": "fill_blank", "question": "Q?", '
          '"correct_answers": ["{ x^{2} + y"]}\n```\n')
    assert any("unbalanced" in v for v in lint_md("teacher-pack", md))


def test_undelimited_caret_prose_flagged():
    md = "Funksiya y = -5t^2 + 15t + 50 ko'rinishida berilgan.\n"
    assert any("undelimited math" in v for v in lint_md("flashcards", md))
    ok = "Funksiya $y = -5t^{2} + 15t + 50$ ko'rinishida berilgan.\n"
    assert lint_md("flashcards", ok) == []


def test_visual_placeholder_lines_exempt_from_caret_check():
    md = ("![visual: diagram — y = x^2 parabola, x va y o'qlari — "
          "image gen required](placeholder)\n")
    assert lint_md("case-based-preview", md) == []
