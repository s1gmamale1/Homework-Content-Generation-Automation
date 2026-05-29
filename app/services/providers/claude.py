"""Anthropic Claude CLI provider.

Wraps the ``claude`` binary (a.k.a. ``claude.cmd`` on Windows). Argv pattern
emits a JSON envelope on stdout via ``--output-format json``; attachments are
passed as positional ``@<path>`` tokens after the prompt.

Source: ``Homework-Automation/autopilot/agent_runner.py`` lines 637-674.
"""

from __future__ import annotations

import json
import pathlib

from .base import Provider


class Claude(Provider):
    name = "claude"
    binary_names = ("claude", "claude.cmd")

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
            "--output-format", "json",
            "--dangerously-skip-permissions",
        ]
        if model:
            argv += ["--model", model]
        # Attachments are positional after any flags; the driver appends the
        # prompt via stdin, so attachments slot in here as ``@<path>``.
        for path in attachments:
            argv.append(f"@{path}")
        return argv

    def parse_envelope(
        self,
        stdout: str,
        *,
        last_msg_path: pathlib.Path,
    ) -> tuple[str, dict]:
        try:
            envelope = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            # Fall back to raw stdout — caller still greps for sentinels.
            return stdout, {
                "prompt_tokens": None,
                "output_tokens": None,
                "cached_tokens": None,
                "total_tokens": None,
                "raw": {},
            }

        if not isinstance(envelope, dict):
            return stdout, {
                "prompt_tokens": None,
                "output_tokens": None,
                "cached_tokens": None,
                "total_tokens": None,
                "raw": {},
            }

        result_text = envelope.get("result") or ""
        usage = envelope.get("usage")
        if not isinstance(usage, dict):
            usage = {}

        prompt_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        # Claude reports cache reads + cache writes separately; cached_tokens
        # is the cache-read portion (the part that costs less).
        cached_tokens = usage.get("cache_read_input_tokens")
        cache_creation = usage.get("cache_creation_input_tokens")

        # Total = all input variants + output. Skip None terms so we still
        # produce a sensible total when only some keys are present.
        components = [
            prompt_tokens,
            output_tokens,
            cached_tokens,
            cache_creation,
        ]
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
        # Claude's pipeline is the byte-identical baseline; no suffix.
        return ""
