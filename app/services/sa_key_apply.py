"""Worker-side: fetch + apply an assigned SA key live (no restart).

Pure-ish units (file/env/http) the worker orchestrates. The capability-global
rebind lives in worker.py to avoid a worker<->this circular import."""
from __future__ import annotations

from pathlib import Path


def upsert_env_file(env_path: Path, updates: dict[str, "str | None"]) -> None:
    """Set/replace each KEY=value in `env_path`, preserving all other lines
    byte-for-byte (UTF-8). A value of None removes that key's line. Creates the
    file if absent."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if ("=" in line and not line.lstrip().startswith("#")) else None
        if key in remaining:
            val = remaining.pop(key)
            if val is not None:
                out.append(f"{key}={val}")
            # None -> drop the line
        else:
            out.append(line)
    for key, val in remaining.items():
        if val is not None:
            out.append(f"{key}={val}")
    env_path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
