"""Provider base class.

A ``Provider`` encapsulates the per-CLI strategy for two pure operations:

1. Building the subprocess argv vector for a given binary + model + last-msg
   sentinel path (and optional attachments).
2. Parsing the CLI's stdout (and, where applicable, the last-msg file) into
   a ``(text, usage)`` envelope with normalized token-usage keys.

Providers do **not** spawn subprocesses themselves. The driver lives in
``app/services/agent.py`` (Wave 2). Keeping providers pure makes them
trivially unit-testable: feed in a stdout fixture, assert on the parsed
envelope.
"""

from __future__ import annotations

import pathlib
from abc import ABC, abstractmethod


class Provider(ABC):
    """Per-CLI subprocess argv builder + envelope parser.

    Concrete subclasses live in sibling modules (``claude.py``, ``kimi.py``,
    ``codex.py``, ``gemini.py``) and are registered in ``__init__.PROVIDERS``.
    """

    name: str = ""
    binary_names: tuple[str, ...] = ()
    # Whether the driver feeds the master prompt on the CLI's stdin (the default
    # for claude/gemini/codex/kimi). Providers whose CLI takes the prompt as a
    # positional argument instead (opencode: ``opencode run "<msg>"``) set this
    # False, and the driver appends the prompt as the final argv token.
    prompt_on_stdin: bool = True

    @abstractmethod
    def build_argv(
        self,
        *,
        binary: str,
        model: str | None,
        last_msg_path: pathlib.Path,
        attachments: list[pathlib.Path] = (),
    ) -> list[str]:
        """Return the argv list for spawning this provider's CLI.

        ``binary`` is the resolved executable path (caller is responsible for
        ``shutil.which``-style resolution against ``binary_names``).
        ``model`` is optional; when falsy, no ``--model`` flag is injected
        (default-model selection lives in the driver, not here).
        ``last_msg_path`` is a sentinel file some providers (codex) use to
        capture clean assistant text out-of-band from stdout.
        ``attachments`` is an optional list of file paths to pass through to
        the CLI (claude supports this via positional ``@<path>`` tokens; other
        providers may ignore it).
        """
        raise NotImplementedError

    @abstractmethod
    def parse_envelope(
        self,
        stdout: str,
        *,
        last_msg_path: pathlib.Path,
    ) -> tuple[str, dict]:
        """Parse the CLI's stdout into ``(result_text, usage)``.

        ``usage`` is a normalized dict with these keys (always present, may
        be ``None`` or ``0`` if the provider does not surface them):

        - ``prompt_tokens``  — input/prompt tokens
        - ``output_tokens``  — completion/output tokens
        - ``cached_tokens``  — cache-read tokens
        - ``total_tokens``   — sum (or provider-reported total when given)
        - ``raw``            — the full provider-specific envelope dict, kept
                               verbatim for debugging / cost reconciliation.
        """
        raise NotImplementedError

    def prompt_suffix(self, ctx: object) -> str:
        """Extra text appended to the master prompt (visual-policy guidance).

        ``ctx`` is a generic placeholder — concrete providers may inspect it
        for build-time data (e.g. an images directory) once the driver in
        Wave 2 settles on a concrete shape. Default returns empty string.
        """
        return ""

    def format_attachments(
        self, attachments: list[pathlib.Path] = ()
    ) -> str:
        """Return a prompt-prefix string that points the CLI at attachments.

        Used by ``agent._build_master_prompt`` to prepend a per-CLI
        attachment preamble (e.g. ``"Files: @/abs/path.pdf\\n\\n"`` for Gemini,
        or a ``"Read the file at /abs/path.pdf"`` instruction for CLIs whose
        only file-access surface is a shell/read_file tool).

        Default implementation returns empty string — used by providers
        (e.g. Claude) that pass attachments via argv tokens instead of
        prompt text. Override in subclasses that need prompt-level wiring.
        """
        return ""
