"""Clodex API-only provider stub.

Clodex uses an OpenAI-compatible HTTP API but has no CLI lane in this system.
The stub lets generic provider resolution and prompt composition work before
``agent._spawn_once`` dispatches API calls to ``api_transport``.
"""

from __future__ import annotations

import pathlib

from .base import Provider


class Clodex(Provider):
    name = "clodex"
    binary_names: tuple[str, ...] = ()

    def build_argv(
        self,
        *,
        binary: str,
        model: str | None,
        last_msg_path: pathlib.Path,
        attachments: list[pathlib.Path] = (),
    ) -> list[str]:
        raise RuntimeError("clodex is api-only")

    def parse_envelope(
        self,
        stdout: str,
        *,
        last_msg_path: pathlib.Path,
    ) -> tuple[str, dict]:
        raise RuntimeError("clodex is api-only")

    def format_attachments(self, attachments: list[pathlib.Path] = ()) -> str:
        return ""

    def prompt_suffix(self, ctx: object) -> str:
        return ""
