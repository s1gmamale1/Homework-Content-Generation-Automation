"""Credential-safe Clodex model probe.

Usage: uv run python scripts/probe_clodex_models.py [--smoke-model MODEL]
"""

from __future__ import annotations

import argparse
import asyncio
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

BASE_URL = "https://clodex.xyz/v1"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-model")
    args = parser.parse_args()
    load_dotenv()
    key = os.environ.get("CLODEX_API_KEY")
    if not key:
        raise SystemExit("CLODEX_API_KEY is unset")
    client = AsyncOpenAI(
        api_key=key,
        base_url=os.environ.get("CLODEX_BASE_URL") or BASE_URL,
    )
    models = await client.models.list()
    print("models:")
    for model in models.data:
        print(f"- {model.id}")
    if args.smoke_model:
        response = await client.chat.completions.create(
            model=args.smoke_model,
            max_completion_tokens=8,
            messages=[{"role": "user", "content": "Reply only: OK"}],
        )
        usage = response.usage
        print(
            "smoke:",
            {
                "requested_model": args.smoke_model,
                "served_model": response.model,
                "finish_reason": response.choices[0].finish_reason,
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
            },
        )


if __name__ == "__main__":
    asyncio.run(main())
