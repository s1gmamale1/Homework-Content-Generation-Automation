"""OpenAI api-only provider stub.

``openai`` is the first **api-only** provider (fleet-api-5 successor,
openai-api-provider plan, task 3): there is no OpenAI CLI, so unlike every
other ``Provider`` subclass this one is never spawned as a subprocess.
``transport=api`` calls go straight through ``app.services.api_transport``
(the ``openai`` SDK branch, shipped in task 1) — ``_spawn_once`` dispatches
to the SDK before ever reaching ``build_argv``/``_resolve_binary``.

This stub still needs to exist in the registry because:

1. ``get_provider("openai")`` is resolved by name (``run_phase`` et al.)
   BEFORE the transport is known to be api vs. cli — the registry lookup
   has to succeed either way.
2. ``run_phase`` calls ``prov.prompt_suffix(None)`` and
   ``prov.format_attachments(...)`` to compose the master prompt BEFORE
   dispatching to ``_spawn`` — these must return ``""`` (like claude/gemini)
   so prompt composition for an api openai call neither raises nor appends
   a stray CLI-oriented suffix.

``build_argv``/``parse_envelope`` are the CLI-only surface. They raise
loudly (never silently no-op) if ever reached, but in practice they are NOT
the runtime backstop against a misrouted cli-transport openai job — that
backstop is ``binary_names = ()``, which makes ``_resolve_binary``
(agent.py:250-259) raise ``FileNotFoundError`` first, before ``build_argv``
is even called. Validation (``validate_transport`` / the api-only rule,
task 2) is what actually prevents a cli-transport openai job from being
created in the first place.
"""

from __future__ import annotations

import pathlib

from .base import Provider


class OpenAiApi(Provider):
    name = "openai"
    binary_names: tuple[str, ...] = ()

    def build_argv(
        self,
        *,
        binary: str,
        model: str | None,
        last_msg_path: pathlib.Path,
        attachments: list[pathlib.Path] = (),
    ) -> list[str]:
        raise RuntimeError("openai is api-only")

    def parse_envelope(
        self,
        stdout: str,
        *,
        last_msg_path: pathlib.Path,
    ) -> tuple[str, dict]:
        raise RuntimeError("openai is api-only")

    def format_attachments(
        self, attachments: list[pathlib.Path] = ()
    ) -> str:
        # Mirrors claude/gemini: attachments for openai's api transport are
        # rejected outright by api_transport.generate (no attachment support
        # yet), so there is no prompt-level preamble to compose here.
        return ""

    def prompt_suffix(self, ctx: object) -> str:
        # No CLI visual-policy tax to carry — openai never runs the CLI path.
        return ""
