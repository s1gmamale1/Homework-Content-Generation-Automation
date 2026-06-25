"""Acceptance smoke for concurrency-knob-1 Phase 1 (reactive 429 backoff).

Drives ONE real gemini api call THROUGH the new `_spawn` retry wrapper to prove
the happy path is unbroken (the wrapper passes success straight through). Minimal
tokens; no DB. Run with:  uv run python -m scripts.smoke_429_backoff
"""
import asyncio

from app.config import settings  # noqa: F401 — import triggers load_dotenv(.env)
from app.services import agent
from app.services.providers import get_provider


async def main() -> None:
    prov = get_provider("gemini")
    rc, text, usage, stderr = await agent._spawn(
        provider=prov,
        model="gemini-2.5-flash",
        prompt="Reply with exactly one word: OK",
        attachments=[],
        transport="api",
    )
    print(f"rc={rc!r}")
    print(f"text={text[:200]!r}")
    print(f"output_tokens={usage.get('output_tokens')} total_tokens={usage.get('total_tokens')}")
    print(f"stderr={stderr[:300]!r}")
    assert rc == 0, f"expected success rc=0, got rc={rc} stderr={stderr[:300]!r}"
    assert text.strip(), "expected non-empty content through the retry wrapper"
    print("SMOKE PASS: happy path returns content through _spawn retry wrapper")


if __name__ == "__main__":
    asyncio.run(main())
