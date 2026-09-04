"""Pre-publish LaTeX lint — generator side of the platform LaTeX subset
contract v1 (2026-09-03, importer-lane ACK).

Deterministic checks over the phase markdown a job is about to publish.
`archive_job` runs `lint_phases` before any Notion I/O and, on violations,
records a skip reason instead of pushing — same block-don't-publish shape as
the leaf-integrity gate, so a violating output is regenerated rather than
shipped. `force=True` (operator override) bypasses the lint.

The command allowlist mirrors the contract text injected into every prompt
(`prompts._NOTATION_EXACT`): anything outside it renders as literal letters on
the platform, so it is a publish-blocking defect, not a style nit.
"""

from __future__ import annotations

import json
import re

_GREEK = {
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta",
    "theta", "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma",
    "tau", "upsilon", "phi", "varphi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon",
    "Phi", "Psi", "Omega",
}

# Platform LaTeX subset contract v1 — keep in lockstep with _NOTATION_EXACT
# and the renderer's test suite. Aliases (\ge \le \ne) are accepted on both
# sides. \, and \% are backslash-punctuation, not commands, and pass the
# command scan untouched.
ALLOWED_COMMANDS = _GREEK | {
    # structure
    "frac", "sqrt", "text", "left", "right",
    # operators
    "cdot", "times", "pm", "div",
    # relations (+ aliases)
    "neq", "leq", "geq", "approx", "lt", "gt", "ne", "le", "ge",
    # arrows / sets
    "to", "implies", "in", "notin", "mathbb",
    # spacing / dots
    "quad", "dots",
    # symbols
    "oplus", "circ", "infty", "sum", "int",
    "angle", "triangle", "parallel", "perp", "vec", "overline",
    # functions (incl. the Russian-tradition names the uz textbooks use)
    "sin", "cos", "tan", "log", "ln", "lg", "tg", "ctg",
    "arcsin", "arccos", "arctan", "arctg", "arcctg",
    "min", "max", "operatorname",
    # sets (renderer-supported; grade-9 domains need interval unions)
    "cup", "cap", "subset", "emptyset",
}

_MATHBB_OK = {"N", "Z", "Q", "R"}

_CMD_RE = re.compile(r"\\([a-zA-Z]+)")
_SPAN_RE = re.compile(r"\$\$[^$]+\$\$|\$[^$\n]+\$")
# Teacher-pack fences, both tolerated shapes: kind on the fence line and kind
# on the first line inside the fence.
_FENCE_RE = re.compile(
    r"```[^\n`]*\nELEMENT: \w+\n.*?\n```|```ELEMENT: \w+\n.*?\n```", re.S)
_ELEM_BODY_RE = re.compile(
    r"```(?:ELEMENT: (\w+)\n|[^\n`]*\nELEMENT: (\w+)\n)(\{.*?\})\s*\n?```", re.S)
_RAW_SINGLE_BS = re.compile(r"(?<!\\)\\(?=[a-zA-Z])")
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
# Memory-check fill_blank typed answers (owner rule 2026-09-03): a plain word
# or number only — a symbolic answer means the question is a formula question
# and must be authored as multiple_choice with formula options instead.
_FILL_ANSWER_RE = re.compile(
    r"\*\*(?:Kutilayotgan javob|Muqobil javoblar):\*\*\s*(.+)")
_LETTER_SLASH_RE = re.compile(r"[A-Za-z]/[A-Za-z]")
_SPACED_OP_RE = re.compile(r" [+\-·×*/÷] ")
# Keyboard-form answer keys (importer directive 2026-09-04): the student's
# answer comes out of the app's math keyboard, so a typed key must be spelled
# exactly as the keyboard serialises it — braced scripts, backslashed function
# names and greek, no \operatorname (the keyboard parser lacks it), balanced
# braces, no $ delimiters (a key is a math field, not prose).
_SCRIPT_PAREN_RE = re.compile(r"[\w)\]}]\s*[\^_]\(")
_BARE_FUNC_RE = re.compile(
    r"(?<![\\A-Za-z])(arcsin|arccos|arctan|arctg|arcctg|sin|cos|tan|tg|ctg"
    r"|log|ln|lg)\s*\(")
_BARE_GREEK_RE = re.compile(
    r"(?<![\\A-Za-z])(alpha|beta|gamma|theta|pi|phi|omega)(?![A-Za-z])")
_MATH_SIGNAL_RE = re.compile(r"[\\^_=]|\d\s*/\s*\d")
_BARE_CARET_RE = re.compile(r"[\w)\]}]\^[\w{(]")
# A step/checkpoint must stand on its own (directive 2026-09-04 §6): a body
# line that points at another step by number is the unanswerable-backreference
# shape — the platform reveals nothing and offers no retry after a wrong pick.
_STEP_BACKREF_RE = re.compile(
    r"\d\s*-\s*(?:qadam|bosqich|nazorat)"
    r"|(?:step|checkpoint)\s*\d"
    r"|шаг[а-яё]*\s*\d|\d\s*-\s*шаг"
    r"|контрольн[а-яё]*\s+точк[а-яё]*\s*\d",
    re.IGNORECASE)
# Element-JSON fields that hold TYPED student answers. `correct_answers` is
# typed only for these element types (tap/select answers copy option text
# verbatim, math span included).
_TYPED_ANSWER_KINDS = {"short_text", "fill_blank"}


def _check_answer_key(val: str, where: str, out: list[str]) -> None:
    if "$" in val:
        out.append(f"{where}: $ delimiter inside typed answer key")
    if "\\operatorname" in val:
        out.append(f"{where}: \\operatorname in answer key — keyboard has no "
                   "\\operatorname; write \\tg, \\sin …")
    if _SCRIPT_PAREN_RE.search(val):
        out.append(f"{where}: parenthesised script ^(…)/_(…) — keyboard form "
                   "is ^{…} (e.g. 64^{\\frac{2}{3}})")
    if _BARE_FUNC_RE.search(val):
        out.append(f"{where}: bare function name in answer key — write "
                   "\\sin(, \\tg( … (bare names render as letters)")
    if _MATH_SIGNAL_RE.search(val) and _BARE_GREEK_RE.search(val):
        out.append(f"{where}: bare greek word in answer key — write \\alpha …")
    if val.count("{") != val.count("}") or val.count("(") != val.count(")"):
        out.append(f"{where}: unbalanced braces/parens in answer key")


def _walk_answer_keys(node, where: str, out: list[str]) -> None:
    if isinstance(node, dict):
        kind = node.get("type")
        for k, v in node.items():
            if k == "expected" and isinstance(v, str):
                _check_answer_key(v, f"{where}.{k}", out)
            elif (k in ("accepted_variants", "answers", "word_bank")
                    and isinstance(v, list)):
                for item in v:
                    if isinstance(item, str):
                        _check_answer_key(item, f"{where}.{k}", out)
            elif (k == "correct_answers" and isinstance(v, list)
                    and kind in _TYPED_ANSWER_KINDS):
                for item in v:
                    if isinstance(item, str):
                        _check_answer_key(item, f"{where}.{k}", out)
            else:
                _walk_answer_keys(v, where, out)
    elif isinstance(node, list):
        for item in node:
            _walk_answer_keys(item, where, out)


def _scan_commands(text: str, where: str, out: list[str]) -> None:
    for m in _CMD_RE.finditer(text):
        name = m.group(1)
        if name not in ALLOWED_COMMANDS:
            out.append(f"{where}: command \\{name} outside contract v1")
        elif name == "mathbb":
            arg = re.match(r"\\mathbb\{([A-Za-z])\}", text[m.start():])
            if not arg or arg.group(1) not in _MATHBB_OK:
                out.append(f"{where}: \\mathbb argument outside N/Z/Q/R")


def _nested_frac(span: str) -> bool:
    """True if a \\frac occurs inside another \\frac's argument braces."""
    for m in re.finditer(r"\\frac", span):
        depth, i = 0, m.end()
        args_seen = 0
        while i < len(span) and args_seen < 2:
            c = span[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    args_seen += 1
            elif depth > 0 and span.startswith("\\frac", i):
                return True
            i += 1
    return False


def lint_md(phase_name: str, md: str) -> list[str]:
    out: list[str] = []
    body = md

    # Teacher-pack ELEMENT/image fences: JSON must parse, backslashes must be
    # doubled in the RAW body, and the DECODED strings obey the same command
    # contract. Fence bodies are removed before the markdown-level checks.
    if phase_name in ("teacher-pack", "teacher-deck"):
        for fm in _ELEM_BODY_RE.finditer(md):
            kind = fm.group(1) or fm.group(2)
            raw = fm.group(3)
            if kind != "image" and _RAW_SINGLE_BS.search(raw):
                out.append(f"{phase_name}/fence({kind}): raw single backslash "
                           "in ELEMENT JSON (must be doubled)")
                continue
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError as exc:
                out.append(f"{phase_name}/fence({kind}): invalid JSON ({exc})")
                continue
            if kind != "image":
                flat = json.dumps(decoded, ensure_ascii=False)
                if any(c in flat for c in "\f\t\b"):
                    out.append(f"{phase_name}/fence({kind}): control character "
                               "after decode (corrupted escape)")
                _scan_commands(flat, f"{phase_name}/fence({kind})", out)
                _walk_answer_keys(decoded, f"{phase_name}/fence({kind})", out)
        body = _FENCE_RE.sub("", md)

    # Hidden QA/answer-key HTML comments are invisible to students, stripped
    # by the importer, and the one place raw quoting/English is allowed — the
    # markdown-level checks don't apply inside them.
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)

    # Banned delimiters anywhere.
    if "\\(" in body or "\\[" in body:
        out.append(f"{phase_name}: \\(..\\)/\\[..\\] delimiters (only $ / $$)")

    # Per-line $ balance — also catches a span crossing a newline.
    for n, line in enumerate(body.splitlines(), 1):
        if line.count("$") % 2 == 1:
            out.append(f"{phase_name}:{n}: unbalanced $ on line")

    # Command allowlist inside spans; nested \frac.
    spans = _SPAN_RE.findall(body)
    for s in spans:
        _scan_commands(s, phase_name, out)
        if _nested_frac(s):
            out.append(f"{phase_name}: nested \\frac")
        if re.search(r"_{3,}", s):
            out.append(f"{phase_name}: ____ blank inside $ span")

    # Bare LaTeX outside any span renders as literal text — the classic mangle.
    # Backticked segments are typed-answer material (checked separately above)
    # and exempt from the prose-level scans.
    prose = _BACKTICK_RE.sub(" ", _SPAN_RE.sub(" ", body))
    for m in _CMD_RE.finditer(prose):
        out.append(f"{phase_name}: bare \\{m.group(1)} outside $ span")

    # Undelimited ASCII math in prose (`y = -5t^2 + 15t + 50`) reaches the
    # student as a literal caret — maths lives inside $…$. Image-placeholder
    # lines (`![visual: …](placeholder)`) feed the image generator, are never
    # rendered to the student, and are exempt.
    for line in prose.splitlines():
        if line.lstrip().startswith("!["):
            continue
        if _BARE_CARET_RE.search(line):
            out.append(f"{phase_name}: undelimited math outside $ span "
                       f"({line.strip()[:50]!r})")

    # Memory-check (owner rule 2026-09-03): formula questions are
    # multiple_choice, never fill_blank — reject symbolic typed answers and
    # in-span placeholders that smuggle a formula-completion past the blank
    # rule. Spans are already paired left-to-right by _SPAN_RE, so a blank
    # BETWEEN two spans (a word blank) never matches.
    if phase_name == "memory-check":
        for s in spans:
            if "?" in s or "\\square" in s:
                out.append("memory-check: placeholder inside $ span — formula "
                           "completion must be multiple_choice")
        for m in _FILL_ANSWER_RE.finditer(body):
            for val in m.group(1).split(","):
                val = val.strip()
                if not val:
                    continue
                if (any(c in val for c in "\\_^{}$=")
                        or _LETTER_SLASH_RE.search(val)
                        or _SPACED_OP_RE.search(val)):
                    out.append(
                        "memory-check: symbolic fill_blank answer "
                        f"({val[:30]!r}) — formula questions must be "
                        "multiple_choice")

    # Multi-step surfaces: a step's body may reference the case, given data, or
    # a result it states itself — never another step by number (headings carry
    # their own step number legitimately and are exempt).
    if phase_name in ("practice-rlc", "case-based-preview"):
        for line in body.splitlines():
            if line.lstrip().startswith("#"):
                continue
            if _STEP_BACKREF_RE.search(line):
                out.append(f"{phase_name}: step referenced by number in step "
                           f"body ({line.strip()[:50]!r})")

    # Typed-answer fields: ED backticked accepted variants are compared against
    # what the math keyboard emits, so they follow keyboard form (directive
    # 2026-09-04) — backslashed commands from the contract vocabulary are now
    # legal there; $ delimiters, \operatorname, parenthesised scripts and bare
    # function/greek names are not.
    if phase_name == "practice-error-detection":
        for t in _BACKTICK_RE.findall(md):
            _check_answer_key(t, f"{phase_name}/variant", out)
            _scan_commands(t, f"{phase_name}/variant", out)

    return out


def lint_phases(phases: list[tuple[str, str]]) -> list[str]:
    """[(phase_name, output_md)] -> flat violation list (empty = publishable)."""
    out: list[str] = []
    for name, md in phases:
        out.extend(lint_md(name, md or ""))
    return out
