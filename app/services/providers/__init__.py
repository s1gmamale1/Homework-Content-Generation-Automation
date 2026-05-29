"""CLI-subprocess provider abstraction.

Each ``Provider`` encapsulates one CLI's argv-building + stdout-parsing
strategy. The actual subprocess driver lives in ``app/services/agent.py``
(Wave 2); providers stay pure so they can be unit-tested with stdout
fixtures.

Public surface:

- ``Provider`` — abstract base class (re-exported for type hints)
- ``PROVIDERS`` — name → provider-instance registry
- ``get_provider(name)`` — registry lookup with a clear error
"""

from __future__ import annotations

from .base import Provider
from .claude import Claude
from .codex import Codex
from .gemini import Gemini
from .kimi import Kimi


CLAUDE = Claude()
KIMI = Kimi()
CODEX = Codex()
GEMINI = Gemini()

PROVIDERS: dict[str, Provider] = {
    p.name: p for p in (CLAUDE, KIMI, CODEX, GEMINI)
}


def get_provider(name: str) -> Provider:
    """Look up a provider by name. Raises ``KeyError`` with a helpful message
    listing the registered providers when ``name`` is not registered."""
    try:
        return PROVIDERS[name]
    except KeyError:
        available = ", ".join(sorted(PROVIDERS))
        raise KeyError(
            f"unknown provider {name!r}; choose from: {available}"
        ) from None


__all__ = [
    "Provider",
    "Claude",
    "Kimi",
    "Codex",
    "Gemini",
    "CLAUDE",
    "KIMI",
    "CODEX",
    "GEMINI",
    "PROVIDERS",
    "get_provider",
]
