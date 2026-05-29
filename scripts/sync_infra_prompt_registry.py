"""Pin the Infra prompt registry → docs/infra_prompt_registry_pinned/manifest.json.

Walks the registered Infra prompts (app.services.prompt_registry.build_manifest)
and writes a pinned manifest (relative path + version + sha256 + size per entry)
so prompt changes are detectable and traceable. Re-run whenever the files under
docs/Infra_prompts/ change (Phase 2 D.1).

Usage:  python scripts/sync_infra_prompt_registry.py
"""

import json
import sys
from pathlib import Path

# Make the `app` package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import prompt_registry  # noqa: E402

OUT = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "infra_prompt_registry_pinned"
    / "manifest.json"
)


def main() -> int:
    manifest = prompt_registry.build_manifest()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    missing = [k for k, v in manifest["entries"].items() if not v["exists"]]
    print(f"wrote {OUT}")
    print(f"  entries={manifest['entry_count']}  missing={missing}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
