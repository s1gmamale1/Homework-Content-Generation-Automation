"""Moonshot Kimi CLI provider.

Wraps the ``kimi`` binary (``kimi.exe`` on Windows). Uses ``stream-json``
output format which emits one JSON object per assistant message on its own
line. Token usage is **not** surfaced by kimi 1.30 in stream-json mode, so
all token counts come back as zero with the raw envelope preserved.

Source: ``Homework-Automation/autopilot/agent_runner.py`` lines 675-733.
"""

from __future__ import annotations

import json
import pathlib

from .base import Provider


_KIMI_VISUAL_SUFFIX = (
    "\n\nVisual policy: delegate raster diagrams to $imagegen via Codex; "
    "SVG inline only.\n"
)


class Kimi(Provider):
    name = "kimi"
    binary_names = ("kimi", "kimi.exe")

    def build_argv(
        self,
        *,
        binary: str,
        model: str | None,
        last_msg_path: pathlib.Path,
        attachments: list[pathlib.Path] = (),
    ) -> list[str]:
        # `--print` enables non-interactive mode (input on stdin, output on
        # stdout) and is required before `--output-format` is accepted.
        # Implies `--yolo` per kimi 1.30 docs.
        argv: list[str] = [
            binary,
            "--print",
            "--output-format", "stream-json",
        ]
        if model:
            argv += ["--model", model]
        # Widen Kimi's workspace scope to cover each attachment's parent dir
        # so its ``ReadFile`` / ``Shell`` tools are allowed to access them.
        # Files already inside the spawn-time cwd are in-scope by default;
        # this is the safety net for absolute paths that live elsewhere.
        seen_parents: set[str] = set()
        for path in attachments:
            parent = str(pathlib.Path(path).resolve().parent)
            if parent in seen_parents:
                continue
            seen_parents.add(parent)
            argv += ["--add-dir", parent]
        return argv

    def format_attachments(
        self, attachments: list[pathlib.Path] = ()
    ) -> str:
        """Kimi has no in-prompt ``@<path>`` syntax. Its ``ReadFile`` tool
        works on text files; for binary PDFs it must shell out to Python
        (pdfplumber is generally available on dev machines) to extract text.
        Inject an explicit instruction naming each attachment + the strategy
        per-extension."""
        if not attachments:
            return ""
        text_paths: list[str] = []
        pdf_paths: list[str] = []
        for p in attachments:
            resolved = pathlib.Path(p).resolve()
            if resolved.suffix.lower() == ".pdf":
                pdf_paths.append(str(resolved))
            else:
                text_paths.append(str(resolved))

        lines: list[str] = ["## ATTACHED FILES"]
        if text_paths:
            lines.append("Text files (use the ReadFile tool):")
            for p in text_paths:
                lines.append(f"- {p}")
        if pdf_paths:
            lines.append(
                "PDF files (use the Shell tool with a python snippet — "
                "``pdfplumber`` is the preferred library, falling back to "
                "``pypdf`` / ``PyPDF2`` if needed; write extracted text to "
                "a UTF-8 .txt file then ReadFile it back to avoid console "
                "encoding errors):"
            )
            for p in pdf_paths:
                lines.append(f"- {p}")
        lines.append(
            "Ground every claim in the actual contents of these files; "
            "do not guess. If extraction fails, say so explicitly rather "
            "than fabricating content."
        )
        lines.append("")
        return "\n".join(lines) + "\n"

    def parse_envelope(
        self,
        stdout: str,
        *,
        last_msg_path: pathlib.Path,
    ) -> tuple[str, dict]:
        # stream-json emits one JSON object per assistant message:
        #   {"role":"assistant","content":[{"type":"text","text":"..."}]}
        # Plus a human-readable banner ("To resume this session: kimi -r ...")
        # which we filter out by requiring lines to start with '{'. Token
        # usage is not surfaced in stream-json (kimi 1.30); metrics are zero.
        text_parts: list[str] = []
        events: list[dict] = []
        for raw in stdout.splitlines():
            line = raw.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(obj)
            # Only collect text from assistant messages. Tool messages
            # (``role == "tool"``) carry ReadFile/Shell results back to the
            # model — leaking those into ``result_text`` would, e.g., embed
            # ``<system>1 lines read...</system>`` framing into a structured
            # phase output and explode Pydantic validation. System / result
            # framing messages also live on stdout and must be ignored.
            if obj.get("role") != "assistant":
                continue
            content = obj.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and part.get("text") is not None:
                    text_parts.append(part["text"])

        result_text = "\n".join(text_parts)
        return result_text, {
            "prompt_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "raw": {"events": events},
        }

    def prompt_suffix(self, ctx: object) -> str:
        return _KIMI_VISUAL_SUFFIX
