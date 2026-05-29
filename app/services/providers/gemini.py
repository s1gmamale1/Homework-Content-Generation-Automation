"""Google Gemini CLI provider.

Wraps the ``gemini`` binary (``gemini.cmd`` on Windows). Gemini prints
warnings ("256-color support not detected", "YOLO mode enabled", "Ripgrep
is not available", etc.) to stdout BEFORE the JSON envelope, so naive
``json.loads(stdout)`` fails. We find the first ``{`` and parse from there.

Envelope shape::

    {
      "session_id": "...",
      "response": "...",
      "stats": {
        "models": {
          "<model_id>": {
            "tokens": {"input": N, "candidates": N, "cached": N,
                       "thoughts": N, "total": N, ...},
            ...
          }
        },
        "tools": {...},
        "files": {...}
      }
    }

Source: ``Homework-Automation/autopilot/agent_runner.py`` lines 793-862.
"""

from __future__ import annotations

import json
import pathlib

from .base import Provider


class Gemini(Provider):
    name = "gemini"
    binary_names = ("gemini", "gemini.cmd")

    def build_argv(
        self,
        *,
        binary: str,
        model: str | None,
        last_msg_path: pathlib.Path,
        attachments: list[pathlib.Path] = (),
    ) -> list[str]:
        argv: list[str] = [
            binary,
            "-o", "json",
            "-p", "",
        ]
        if model:
            argv += ["--model", model]
        # Gemini CLI sandboxes ``@<path>`` references to the workspace + project
        # temp dir. To let it resolve absolute paths to PDFs (or any file
        # outside cwd), we widen the sandbox via ``--include-directories``,
        # one entry per *parent* directory of each attachment. Duplicates are
        # de-duped to keep argv compact.
        seen_parents: set[str] = set()
        for path in attachments:
            parent = str(pathlib.Path(path).resolve().parent)
            if parent in seen_parents:
                continue
            seen_parents.add(parent)
            argv += ["--include-directories", parent]
        return argv

    def format_attachments(
        self, attachments: list[pathlib.Path] = ()
    ) -> str:
        """Gemini reads files referenced as ``@<absolute-path>`` in the prompt
        body, provided the parent dir is on the sandbox allow-list (handled
        in :meth:`build_argv`)."""
        if not attachments:
            return ""
        refs = " ".join(f"@{pathlib.Path(p).resolve()}" for p in attachments)
        return (
            "## ATTACHED FILES\n"
            f"{refs}\n"
            "Read the attached file(s) above to ground your answer in their "
            "actual contents. Do not guess or fabricate — if you cannot read "
            "a file, say so explicitly.\n\n"
        )

    def parse_envelope(
        self,
        stdout: str,
        *,
        last_msg_path: pathlib.Path,
    ) -> tuple[str, dict]:
        empty = {
            "prompt_tokens": None,
            "output_tokens": None,
            "cached_tokens": None,
            "total_tokens": None,
            "raw": {},
        }

        idx = stdout.find("{")
        if idx < 0:
            return stdout, empty
        try:
            envelope = json.loads(stdout[idx:])
        except json.JSONDecodeError:
            return stdout, empty
        if not isinstance(envelope, dict):
            return stdout, empty

        result_text = envelope.get("response") or ""

        # Pull the first model's token stats — typically there's exactly one.
        stats = envelope.get("stats") or {}
        models = stats.get("models") if isinstance(stats, dict) else None
        tokens: dict = {}
        if isinstance(models, dict) and models:
            first = next(iter(models.values()))
            t = first.get("tokens") if isinstance(first, dict) else None
            if isinstance(t, dict):
                tokens = t

        prompt_tokens = tokens.get("input") or tokens.get("prompt")
        output_tokens = tokens.get("candidates") or tokens.get("output")
        cached_tokens = tokens.get("cached")
        total_tokens = tokens.get("total")
        if total_tokens is None:
            components = [prompt_tokens, output_tokens, cached_tokens]
            total = sum(c for c in components if isinstance(c, int))
            total_tokens = total if total > 0 else None

        return result_text, {
            "prompt_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "total_tokens": total_tokens,
            "raw": envelope,
        }

    def prompt_suffix(self, ctx: object) -> str:
        # Gemini is used today as the cheap up-front extractor; no
        # visual-policy suffix needed for that role.
        return ""
