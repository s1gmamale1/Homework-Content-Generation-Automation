"""opencode CLI provider (https://opencode.ai/).

Wraps the ``opencode`` binary (``opencode.cmd`` on Windows; installed via
``npm i -g opencode-ai``). Registered as a general provider; its free "zen"
models need no API key, which makes it attractive for the cheap ``extract``
phase (pin via ``EXTRACT_PROVIDER`` / ``EXTRACT_MODEL`` if desired — the
default extractor stays gemini).

Invocation::

    opencode run --format json [-m provider/model] [-f file ...]

with the prompt piped on **stdin** (``run`` reads stdin when given no positional
message — the driver in ``agent._spawn`` pipes the master prompt that way, so
this mirrors how the gemini provider works).

``--format json`` emits a JSONL event stream (one JSON object per line)::

    {"type":"step_start",  "part":{...}}
    {"type":"text",        "part":{"type":"text","text":"...assistant text..."}}
    {"type":"step_finish", "part":{..., "tokens":{"input":N,"output":N,
                                                  "total":N,"cache":{"read":N}}}}

We concatenate every ``text`` part for the result and read token usage from the
last ``step_finish``. Non-JSON lines (cold-start banners / migration notices)
are ignored.
"""

from __future__ import annotations

import json
import pathlib

from .base import Provider


class OpenCode(Provider):
    name = "opencode"
    # ``.cmd`` first so ``shutil.which`` resolves the Windows-executable shim;
    # npm also drops an extensionless bash shim that create_subprocess_exec
    # can't run directly.
    binary_names = ("opencode.cmd", "opencode")

    def build_argv(
        self,
        *,
        binary: str,
        model: str | None,
        last_msg_path: pathlib.Path,
        attachments: list[pathlib.Path] = (),
    ) -> list[str]:
        # Prompt is fed on stdin by the driver (verified: `"<msg>" | opencode
        # run --format json` works). Do NOT switch to opencode's positional
        # `run <msg>` form — our prompts embed page text and reach ~60k chars,
        # which exceeds the Windows command-line length limit (WinError 206).
        argv: list[str] = [binary, "run", "--format", "json"]
        if model:
            argv += ["-m", model]
        for path in attachments:
            argv += ["-f", str(pathlib.Path(path).resolve())]
        return argv

    def format_attachments(self, attachments: list[pathlib.Path] = ()) -> str:
        # Files are passed via ``-f`` argv tokens (build_argv), so no prompt-level
        # preamble is needed — mirror Claude's positional-attachment approach.
        return ""

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

        text_parts: list[str] = []
        tokens: dict = {}
        last_event: dict = {}
        for line in stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(evt, dict):
                continue
            last_event = evt
            part = evt.get("part") or {}
            etype = evt.get("type")
            if etype == "text" and isinstance(part, dict):
                t = part.get("text")
                if isinstance(t, str) and t:
                    text_parts.append(t)
            elif etype == "step_finish" and isinstance(part, dict):
                tk = part.get("tokens")
                if isinstance(tk, dict):
                    tokens = tk

        result_text = "".join(text_parts).strip()
        if not result_text and not tokens:
            # Couldn't parse any events — hand back raw stdout so callers can
            # log/diagnose instead of silently returning empty.
            return stdout.strip(), {**empty, "raw": last_event}

        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        prompt_tokens = tokens.get("input")
        output_tokens = tokens.get("output")
        cached_tokens = cache.get("read") if cache else None
        total_tokens = tokens.get("total")
        if total_tokens is None:
            comps = [c for c in (prompt_tokens, output_tokens) if isinstance(c, int)]
            total_tokens = sum(comps) or None

        return result_text, {
            "prompt_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "total_tokens": total_tokens,
            "raw": tokens or last_event,
        }

    def prompt_suffix(self, ctx: object) -> str:
        return ""
