"""OpenAI Codex CLI provider.

Wraps the ``codex`` binary (``codex.cmd`` on Windows). Codex writes the
clean assistant text into the file passed via ``-o`` (the ``last_msg_path``
sentinel), while stdout carries JSONL events including a ``turn.completed``
event with usage counts.

Source: ``Homework-Automation/autopilot/agent_runner.py`` lines 734-792.
"""

from __future__ import annotations

import json
import pathlib

from .base import Provider


_CODEX_VISUAL_SUFFIX = (
    "\n\nVisual policy: use native $imagegen for raster, SVG inline.\n"
)


class Codex(Provider):
    name = "codex"
    binary_names = ("codex", "codex.cmd")

    def build_argv(
        self,
        *,
        binary: str,
        model: str | None,
        last_msg_path: pathlib.Path,
        attachments: list[pathlib.Path] = (),
    ) -> list[str]:
        argv: list[str] = [
            binary, "exec",
            "--json",
            "-o", str(last_msg_path),
            # Codex defaults to read-only sandbox which is fine for the
            # ``Get-Content`` / ``cat`` calls it uses to ingest files.
            "--skip-git-repo-check",
        ]
        if model:
            argv += ["--model", model]
        # Widen the sandbox to cover each attachment's parent dir so the
        # shell tool is allowed to read them. Codex's primary workspace is
        # the spawn-time cwd (the project root); attachments under
        # ``var/books/...`` are already in-workspace, but adding their parent
        # explicitly is harmless and makes ad-hoc absolute paths work too.
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
        """Codex has no in-prompt ``@<path>`` syntax, but its shell tool can
        read any path inside the sandbox. Inject an explicit instruction
        naming each attachment so the model invokes ``read_file`` / shell
        ``Get-Content`` against it."""
        if not attachments:
            return ""
        paths = [str(pathlib.Path(p).resolve()) for p in attachments]
        bullet = "\n".join(f"- {p}" for p in paths)
        return (
            "## ATTACHED FILES\n"
            f"{bullet}\n"
            "Use your shell tool (e.g. Get-Content / cat / a python script) "
            "to read the attached file(s) above. For PDFs, use a Python "
            "snippet with pdfplumber (or PyPDF2/pypdf) to extract the text. "
            "Ground every claim in their actual contents; do not guess.\n\n"
        )

    def parse_envelope(
        self,
        stdout: str,
        *,
        last_msg_path: pathlib.Path,
    ) -> tuple[str, dict]:
        # Stdout is JSONL events: thread.started / turn.started /
        # item.completed / turn.completed (carrying usage). The clean
        # assistant text lives in the last-message file passed via ``-o``.
        usage_event: dict = {}
        events: list[dict] = []
        for raw in stdout.splitlines():
            line = raw.strip()
            if not line.startswith("{"):
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(evt)
            if evt.get("type") == "turn.completed":
                # Usage may be nested under ``usage`` or under
                # ``payload.tokens`` — check both.
                u = evt.get("usage")
                if isinstance(u, dict):
                    usage_event = u
                else:
                    payload = evt.get("payload")
                    if isinstance(payload, dict):
                        tokens = payload.get("tokens")
                        if isinstance(tokens, dict):
                            usage_event = tokens

        try:
            result_text = (
                last_msg_path.read_text(encoding="utf-8")
                if last_msg_path.is_file()
                else ""
            )
        except OSError:
            result_text = ""

        # Codex's payload uses ``input``/``output``/``cached`` keys in the
        # newer schema and ``input_tokens``/``output_tokens``/``cached_input_tokens``
        # in the older one. Try both.
        prompt_tokens = (
            usage_event.get("input")
            if usage_event.get("input") is not None
            else usage_event.get("input_tokens")
        )
        output_tokens = (
            usage_event.get("output")
            if usage_event.get("output") is not None
            else usage_event.get("output_tokens")
        )
        cached_tokens = (
            usage_event.get("cached")
            if usage_event.get("cached") is not None
            else usage_event.get("cached_input_tokens")
        )

        components = [prompt_tokens, output_tokens, cached_tokens]
        total = sum(c for c in components if isinstance(c, int))
        total_tokens = total if total > 0 else None

        return result_text, {
            "prompt_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "total_tokens": total_tokens,
            "raw": {"events": events, "usage": usage_event},
        }

    def prompt_suffix(self, ctx: object) -> str:
        return _CODEX_VISUAL_SUFFIX
