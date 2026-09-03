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
    # functions
    "sin", "cos", "tan", "log", "ln",
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
    prose = _SPAN_RE.sub(" ", body)
    for m in _CMD_RE.finditer(prose):
        out.append(f"{phase_name}: bare \\{m.group(1)} outside $ span")

    # Typed-answer fields: ED backticked accepted variants are keyboard text.
    if phase_name == "practice-error-detection":
        for t in _BACKTICK_RE.findall(md):
            if "$" in t or "\\" in t:
                out.append(f"{phase_name}: math markup inside backticked "
                           f"accepted variant ({t[:40]!r})")

    return out


def lint_phases(phases: list[tuple[str, str]]) -> list[str]:
    """[(phase_name, output_md)] -> flat violation list (empty = publishable)."""
    out: list[str] = []
    for name, md in phases:
        out.extend(lint_md(name, md or ""))
    return out
