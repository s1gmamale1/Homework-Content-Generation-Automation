# SA-Key Web Upload & Worker Auto-Distribution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload GCP service-account `.json` keys from the web, assign a key to one or many workers, and have each worker pull its assigned key and apply it **live** (no restart) — gaining gemini-api claim capability within ≤30s.

**Architecture:** A `sa_keys` pool table (metadata) + `sa_key_assignments` table (hostname→key) on the head; raw JSON bytes on disk under `var_dir/sa_keys/`. Workers read their assignment from Postgres by **hostname** (they already have a DB connection), pull key **bytes** over the existing auth-gated HTTP file-pull pattern (`book_fetch.py`), and apply live: atomic file write + paired `os.environ` set + **recompute the frozen capability globals** + UTF-8 line-preserving `.env` upsert. Swaps happen only when the worker is idle, so `os.environ` is never mutated mid-spawn.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Postgres, httpx (sync, via `asyncio.to_thread`), React + TanStack Query (FE).

## Approach & key decisions

**Chosen — pull on the existing heartbeat cadence (rejected: a dedicated key-poll loop; rejected: server→worker push/SSE).** Reuse what exists: the worker already reads its own registry state each beat and already pulls files from the head over an auth-gated endpoint. Add a hostname-keyed assignment read + a key-bytes pull. No new channel.

**Load-bearing facts, verified against current code:**
- `CAPABILITIES` (`worker.py:94`) and `CAPABILITY_BLOB` (`worker.py:99`) are computed **once at import** from `os.environ`. The claim gate reads the module global `CAPABILITIES` at call time (`worker.py:345`) and the heartbeat publishes `CAPABILITY_BLOB` (`worker.py:627`). ⇒ Reassigning these globals after a live apply IS seen by the next claim/beat; mutating `os.environ` alone is **not**. This is the single biggest gap — without the rebind, a keyless worker that receives a key stays idle forever.
- `_auth_env` reads `{**os.environ, …}` fresh per spawn (`agent.py:511`) and its Vertex branch **raises** if `GOOGLE_APPLICATION_CREDENTIALS` **or** `GOOGLE_CLOUD_PROJECT` is missing (`agent.py:312–315`). ⇒ The file write must be atomic (`os.replace`) and the two env vars set together; the swap runs only when the worker is idle so no concurrent spawn snapshots a torn state.
- Worker identity `self.id = f"{hostname}:{pid}"` (`worker.py:105`) changes every restart. ⇒ Assignment keys on **bare `socket.gethostname()`**, a separate lookup from the per-pid registry row.
- Creds are read from `os.environ`, never `settings`; `.env` is loaded once via `load_dotenv(override=False)` (`config.py:14`). ⇒ Live apply = set `os.environ` (next spawn sees it) + persist to `.env` (next restart sees it). Both startup-sync and `.env` persistence make a restart re-apply correctly.
- `book_fetch.ensure_book_pdf_sync` (`book_fetch.py:62–132`) is the exact worker→head pull idiom (httpx Bearer + temp file + `os.replace`). Reused for key bytes.
- `get_current_user` accepts both Bearer header AND `?token=` query param (`auth.py:24–37`). ⇒ The key download needs a header-only variant that also refuses to serve when auth is disabled (a credential vault must never be open).

## Global Constraints

- **Latest migration is `0040_books_source_language`** — the new migration is `0041_sa_keys`, `down_revision = "0040_books_source_language"`. Revision id ≤ 32 chars.
- **Never return `private_key`** from any metadata/list endpoint — only the dedicated download route serves raw bytes.
- **Key download is Bearer-header-only and 503s when `valid_auth_tokens()` is empty.** Other sa-key endpoints use the standard `get_current_user` (router-level).
- **All worker file writes honor `settings.var_dir`** via `storage.sa_key_*` helpers — never a hardcoded path. Writes land under `var/` (gitignored, `.gitignore:22`).
- **`.env` upserts are UTF-8 and line-preserving** — rewrite only the two credential keys, leave every other line (including non-ASCII Cyrillic `ru:` keys) byte-for-byte.
- **`os.environ` swaps happen only when the worker is idle** (`len(self._tasks) == 0`).
- **Stage only the files each task lists.** Other sessions commit to this branch; never `git add -A`.
- **Run backend tests with** `uv run python -m pytest <path> -q`. DB-gated tests need a scratch DB: `createdb -U macmini5 <db>` → `DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/<db> RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head` then run pytest with the same two env vars; `dropdb -U macmini5 <db>` after.
- **FE acceptance** = `cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build` (the chunk-size warning is pre-existing).

---

## File Structure

- `alembic/versions/0041_sa_keys.py` — **create** — two tables (`sa_keys`, `sa_key_assignments`).
- `app/models/sa_key.py` — **create** — `SAKey`, `SAKeyAssignment` ORM models.
- `app/services/storage.py` — **modify** — add `sa_key_path(id)` + `sa_key_active_path()`.
- `app/services/sa_key_validate.py` — **create** — pure validate/extract of an SA-key JSON.
- `app/repositories/sa_keys.py` — **create** — pool CRUD + assignment CRUD + `get_assignment_with_key`.
- `app/auth.py` — **modify** — add header-only `get_current_user_strict`.
- `app/api/v1/sa_keys.py` — **create** — upload/list/delete/download + assign/unassign/scrub.
- `app/api/v1/__init__.py` — **modify** — include the new router.
- `app/api/v1/workers.py` — **modify** — surface per-host assignment in the workers list.
- `app/services/sa_key_apply.py` — **create** — pull bytes, atomic write, paired env, `.env` upsert (pure units).
- `app/services/worker.py` — **modify** — `_rebind_capabilities`, `_sync_sa_key`, startup + main-loop hooks.
- `web/src/lib/api.ts`, `web/src/lib/types.ts` — **modify** — client fns + types.
- `web/src/components/fleet/sa-keys-panel.tsx` — **create** — upload + list + per-host assign UI.
- `web/src/routes/fleet.tsx` — **modify** — mount the panel.

---

## Task 1: Migration + models for `sa_keys` and `sa_key_assignments`

**Files:**
- Create: `alembic/versions/0041_sa_keys.py`
- Create: `app/models/sa_key.py`
- Test: `tests/migrations/test_0041_sa_keys.py`

**Interfaces:**
- Produces: tables `sa_keys` (id PK, original_filename, project_id, client_email, sha256 UNIQUE, byte_size, label NULL, created_at) and `sa_key_assignments` (hostname PK, key_id UUID NULL FK→sa_keys.id ON DELETE RESTRICT, scrub_requested_at NULL, updated_at). ORM: `SAKey`, `SAKeyAssignment`.

- [ ] **Step 1: Write the models**

```python
# app/models/sa_key.py
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _utcnow


class SAKey(Base):
    """One uploaded GCP service-account key. The raw JSON (incl. private_key)
    lives on disk at storage.sa_key_path(id); only metadata is stored here."""

    __tablename__ = "sa_keys"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_email: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class SAKeyAssignment(Base):
    """Which SA key a worker host should use. Keyed by bare hostname (stable
    across restarts, unlike workers.pc_id=hostname:pid). key_id NULL +
    scrub_requested_at set = an active 'clear this host's key' signal."""

    __tablename__ = "sa_key_assignments"

    hostname: Mapped[str] = mapped_column(Text, primary_key=True)
    key_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sa_keys.id", ondelete="RESTRICT"),
        nullable=True,
    )
    scrub_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
```

Verify `_utcnow` is exported from `app/models/base.py` (it is — imported by `worker.py:43`). If `Base` import differs, match `app/models/worker.py:8` (`from app.models.base import Base`).

- [ ] **Step 2: Write the migration**

```python
# alembic/versions/0041_sa_keys.py
"""Add sa_keys + sa_key_assignments (web SA-key upload + worker auto-distribution)."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_sa_keys"
down_revision: Union[str, Sequence[str], None] = "0040_books_source_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sa_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("client_email", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sha256", name="uq_sa_keys_sha256"),
    )
    op.create_table(
        "sa_key_assignments",
        sa.Column("hostname", sa.Text(), primary_key=True),
        sa.Column("key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scrub_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["key_id"], ["sa_keys.id"], name="fk_sa_key_assignments_key_id",
            ondelete="RESTRICT",
        ),
    )


def downgrade() -> None:
    op.drop_table("sa_key_assignments")
    op.drop_table("sa_keys")
```

- [ ] **Step 3: Write the failing test**

```python
# tests/migrations/test_0041_sa_keys.py
import os
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


@pytest.mark.asyncio
async def test_sa_keys_tables_and_constraints():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        # tables exist
        for t in ("sa_keys", "sa_key_assignments"):
            got = await conn.scalar(text("SELECT to_regclass(:t)"), {"t": t})
            assert got == t
        # unique sha256: a second identical sha must fail
        kid1, kid2 = uuid.uuid4(), uuid.uuid4()
        await conn.execute(text(
            "INSERT INTO sa_keys (id, original_filename, project_id, client_email, "
            "sha256, byte_size, created_at) VALUES (:id,'a','p','e','SHA',10, now())"
        ), {"id": kid1})
        with pytest.raises(Exception):
            await conn.execute(text(
                "INSERT INTO sa_keys (id, original_filename, project_id, client_email, "
                "sha256, byte_size, created_at) VALUES (:id,'b','p','e','SHA',10, now())"
            ), {"id": kid2})
    # key_id is nullable + FK RESTRICT blocks deleting an assigned key
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO sa_key_assignments (hostname, key_id, updated_at) "
            "VALUES ('host-a', :kid, now())"
        ), {"kid": kid1})
        with pytest.raises(Exception):
            await conn.execute(text("DELETE FROM sa_keys WHERE id=:kid"), {"kid": kid1})
    async with engine.begin() as conn:
        # null key_id (scrub state) is allowed
        await conn.execute(text(
            "INSERT INTO sa_key_assignments (hostname, key_id, scrub_requested_at, updated_at) "
            "VALUES ('host-b', NULL, now(), now())"
        ))
    await engine.dispose()
```

- [ ] **Step 4: Create scratch DB, migrate, run the test**

```bash
createdb -U macmini5 edu_sak_t1
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_sak_t1 RUN_DB_INTEGRATION=1 \
  uv run --extra dev alembic upgrade head
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_sak_t1 RUN_DB_INTEGRATION=1 \
  uv run python -m pytest tests/migrations/test_0041_sa_keys.py -q
```
Expected: PASS. (If alembic can't reach `head`, confirm `0040_books_source_language` is the prior head with `uv run alembic heads`.)

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0041_sa_keys.py app/models/sa_key.py tests/migrations/test_0041_sa_keys.py
git commit -m "feat(sa-keys): migration 0041 + SAKey/SAKeyAssignment models"
```

---

## Task 2: Storage path helpers

**Files:**
- Modify: `app/services/storage.py`
- Test: `tests/services/test_storage_sa_keys.py`

**Interfaces:**
- Produces: `storage.sa_key_path(key_id: UUID | str) -> Path` → `<var_dir>/sa_keys/<id>.json`; `storage.sa_key_active_path() -> Path` → `<var_dir>/sa_keys/active.json`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_storage_sa_keys.py
import importlib
from uuid import uuid4

import app.config as config
import app.services.storage as storage


def test_sa_key_paths_honor_var_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    kid = uuid4()
    assert storage.sa_key_path(kid) == tmp_path / "sa_keys" / f"{kid}.json"
    assert storage.sa_key_active_path() == tmp_path / "sa_keys" / "active.json"
```

- [ ] **Step 2: Run it — FAIL** (`AttributeError: module ... has no attribute 'sa_key_path'`).

Run: `uv run python -m pytest tests/services/test_storage_sa_keys.py -q`

- [ ] **Step 3: Implement** — append to `app/services/storage.py`:

```python
def sa_key_dir() -> Path:
    """Directory holding uploaded SA-key JSONs: ``<var_dir>/sa_keys``."""
    return Path(settings.var_dir) / "sa_keys"


def sa_key_path(key_id: UUID | str) -> Path:
    """On-disk path to one uploaded SA key: ``<var_dir>/sa_keys/<id>.json``."""
    return sa_key_dir() / f"{key_id}.json"


def sa_key_active_path() -> Path:
    """The single key a worker has currently applied: ``<var_dir>/sa_keys/active.json``.
    GOOGLE_APPLICATION_CREDENTIALS points at the resolved absolute form of this."""
    return sa_key_dir() / "active.json"
```

- [ ] **Step 4: Run it — PASS.**
- [ ] **Step 5: Commit**

```bash
git add app/services/storage.py tests/services/test_storage_sa_keys.py
git commit -m "feat(sa-keys): storage path helpers honoring VAR_DIR"
```

---

## Task 3: SA-key JSON validation + field extraction (pure)

**Files:**
- Create: `app/services/sa_key_validate.py`
- Test: `tests/services/test_sa_key_validate.py`

**Interfaces:**
- Produces: `InvalidServiceAccountKey(ValueError)`; `parse_and_validate_sa_key(body: bytes) -> tuple[str, str]` returning `(project_id, client_email)`, raising `InvalidServiceAccountKey` on anything that is not a real SA key.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_sa_key_validate.py
import json
import pytest
from app.services.sa_key_validate import parse_and_validate_sa_key, InvalidServiceAccountKey


def _good() -> bytes:
    return json.dumps({
        "type": "service_account",
        "project_id": "my-proj",
        "client_email": "svc@my-proj.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
    }).encode()


def test_valid_key_returns_project_and_email():
    assert parse_and_validate_sa_key(_good()) == (
        "my-proj", "svc@my-proj.iam.gserviceaccount.com"
    )


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("type"),
    lambda d: d.update(type="authorized_user"),
    lambda d: d.pop("project_id"),
    lambda d: d.pop("client_email"),
    lambda d: d.pop("private_key"),
    lambda d: d.update(project_id=""),
])
def test_rejects_non_sa(mutate):
    d = json.loads(_good())
    mutate(d)
    with pytest.raises(InvalidServiceAccountKey):
        parse_and_validate_sa_key(json.dumps(d).encode())


def test_rejects_non_json():
    with pytest.raises(InvalidServiceAccountKey):
        parse_and_validate_sa_key(b"not json {{{")
```

- [ ] **Step 2: Run it — FAIL** (module missing).
- [ ] **Step 3: Implement**

```python
# app/services/sa_key_validate.py
"""Validate + extract identity from a GCP service-account key JSON. Pure; no I/O."""
from __future__ import annotations

import json


class InvalidServiceAccountKey(ValueError):
    """The uploaded bytes are not a usable GCP service-account key."""


def parse_and_validate_sa_key(body: bytes) -> tuple[str, str]:
    """Return (project_id, client_email) for a valid SA key, else raise.

    A valid key is JSON with type=="service_account" and non-empty
    project_id, client_email and private_key fields."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidServiceAccountKey(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidServiceAccountKey("JSON is not an object")
    if data.get("type") != "service_account":
        raise InvalidServiceAccountKey("type is not 'service_account'")
    project_id = data.get("project_id")
    client_email = data.get("client_email")
    private_key = data.get("private_key")
    if not project_id:
        raise InvalidServiceAccountKey("missing project_id")
    if not client_email:
        raise InvalidServiceAccountKey("missing client_email")
    if not private_key:
        raise InvalidServiceAccountKey("missing private_key")
    return str(project_id), str(client_email)
```

- [ ] **Step 4: Run it — PASS.**
- [ ] **Step 5: Commit**

```bash
git add app/services/sa_key_validate.py tests/services/test_sa_key_validate.py
git commit -m "feat(sa-keys): SA-key JSON validation + field extraction"
```

---

## Task 4: `sa_keys` repository — pool CRUD

**Files:**
- Create: `app/repositories/sa_keys.py`
- Test: `tests/integration/test_sa_keys_repo_pool.py`

**Interfaces:**
- Produces (this task):
  - `create_or_get(session, *, original_filename, project_id, client_email, sha256, byte_size, label=None) -> SAKey` — dedups on sha256 (returns the existing row on a hash hit).
  - `get(session, key_id) -> SAKey | None`
  - `list_keys(session) -> list[dict]` — each `{id, project_id, client_email, original_filename, label, byte_size, created_at, worker_count}`; never includes private-key material.
  - `delete(session, key_id) -> str` — `"deleted"` | `"not_found"` | `"assigned"` (blocked when any assignment references it).

- [ ] **Step 1: Write the failing test** (DB-gated)

```python
# tests/integration/test_sa_keys_repo_pool.py
import os
import pytest
from app.db import SessionLocal
from app.repositories import sa_keys as repo

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


@pytest.mark.asyncio
async def test_create_dedups_on_sha_and_delete_guards_assigned():
    async with SessionLocal() as s:
        async with s.begin():
            a = await repo.create_or_get(
                s, original_filename="k.json", project_id="p1",
                client_email="e1", sha256="SHA-POOL-1", byte_size=10)
            b = await repo.create_or_get(
                s, original_filename="k2.json", project_id="p1",
                client_email="e1", sha256="SHA-POOL-1", byte_size=10)
        assert a.id == b.id  # dedup hit, same row
    async with SessionLocal() as s:
        async with s.begin():
            listed = await repo.list_keys(s)
        assert any(k["id"] == a.id and k["worker_count"] == 0 for k in listed)
        assert all("private_key" not in k for k in listed)
    async with SessionLocal() as s:
        async with s.begin():
            await repo.assign(s, "host-pool", a.id)
        async with s.begin():
            assert await repo.delete(s, a.id) == "assigned"  # blocked
        async with s.begin():
            await repo.unassign(s, "host-pool")
        async with s.begin():
            assert await repo.delete(s, a.id) == "deleted"
```

- [ ] **Step 2: Run it — FAIL** (module missing). Note this test also exercises `assign`/`unassign` from Task 5; it stays RED until Task 5. Until then, assert only the dedup+list portion by splitting the test, OR implement Task 4 and Task 5 repo functions together in this file and run the combined test at the end of Task 5. **Decision for the implementer:** create `app/repositories/sa_keys.py` with the Task-4 functions now; add the assign/unassign functions in Task 5. Run the dedup+list asserts now (comment the assign/delete-guard block), uncomment in Task 5.

- [ ] **Step 3: Implement the pool functions**

```python
# app/repositories/sa_keys.py
from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import _utcnow
from app.models.sa_key import SAKey, SAKeyAssignment


async def create_or_get(
    session: AsyncSession, *, original_filename: str, project_id: str,
    client_email: str, sha256: str, byte_size: int, label: str | None = None,
) -> SAKey:
    existing = await session.scalar(select(SAKey).where(SAKey.sha256 == sha256))
    if existing is not None:
        return existing
    row = SAKey(
        original_filename=original_filename, project_id=project_id,
        client_email=client_email, sha256=sha256, byte_size=byte_size, label=label,
    )
    session.add(row)
    await session.flush()
    return row


async def get(session: AsyncSession, key_id: UUID) -> SAKey | None:
    return await session.get(SAKey, key_id)


async def list_keys(session: AsyncSession) -> list[dict]:
    counts = dict(
        (await session.execute(
            select(SAKeyAssignment.key_id, func.count())
            .where(SAKeyAssignment.key_id.is_not(None))
            .group_by(SAKeyAssignment.key_id)
        )).all()
    )
    rows = (await session.execute(select(SAKey).order_by(SAKey.created_at))).scalars().all()
    return [
        {
            "id": r.id, "project_id": r.project_id, "client_email": r.client_email,
            "original_filename": r.original_filename, "label": r.label,
            "byte_size": r.byte_size, "created_at": r.created_at,
            "worker_count": int(counts.get(r.id, 0)),
        }
        for r in rows
    ]


async def delete(session: AsyncSession, key_id: UUID) -> str:
    row = await session.get(SAKey, key_id)
    if row is None:
        return "not_found"
    assigned = await session.scalar(
        select(func.count()).select_from(SAKeyAssignment)
        .where(SAKeyAssignment.key_id == key_id)
    )
    if assigned and assigned > 0:
        return "assigned"
    await session.execute(sa_delete(SAKey).where(SAKey.id == key_id))
    return "deleted"
```

- [ ] **Step 4: Run it** (scratch DB, dedup+list asserts only) — PASS.

```bash
createdb -U macmini5 edu_sak_t4
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_sak_t4 RUN_DB_INTEGRATION=1 \
  uv run --extra dev alembic upgrade head
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_sak_t4 RUN_DB_INTEGRATION=1 \
  uv run python -m pytest tests/integration/test_sa_keys_repo_pool.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/repositories/sa_keys.py tests/integration/test_sa_keys_repo_pool.py
git commit -m "feat(sa-keys): pool repository (create/dedup/list/delete-guard)"
```

---

## Task 5: `sa_keys` repository — assignment CRUD + worker lookup

**Files:**
- Modify: `app/repositories/sa_keys.py`
- Test: `tests/integration/test_sa_keys_repo_assign.py` (+ uncomment the guarded block in Task 4's test)

**Interfaces:**
- Consumes: `SAKey`, `SAKeyAssignment`.
- Produces:
  - `assign(session, hostname, key_id) -> None` — upsert `(hostname → key_id)`, clears `scrub_requested_at`.
  - `unassign(session, hostname) -> bool` — delete the row (non-destructive: worker keeps its applied key). Returns whether a row existed.
  - `scrub(session, hostname) -> None` — upsert `key_id=NULL, scrub_requested_at=now()` (active clear signal).
  - `get_assignment_with_key(session, hostname) -> dict | None` — `{key_id, sha256, project_id, scrub: bool}` joined to `sa_keys`, or None when no row.
  - `list_assignments(session) -> list[dict]` — `{hostname, key_id, project_id, label}` for the head UI.

- [ ] **Step 1: Write the failing test** (DB-gated)

```python
# tests/integration/test_sa_keys_repo_assign.py
import os
import pytest
from app.db import SessionLocal
from app.repositories import sa_keys as repo

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


@pytest.mark.asyncio
async def test_shared_key_assign_scrub_and_lookup():
    async with SessionLocal() as s:
        async with s.begin():
            k = await repo.create_or_get(
                s, original_filename="k.json", project_id="proj-x",
                client_email="e", sha256="SHA-ASG", byte_size=9)
        # one key shared by two hosts (the flexible shared-key case)
        async with s.begin():
            await repo.assign(s, "host-1", k.id)
            await repo.assign(s, "host-2", k.id)
        async with s.begin():
            a1 = await repo.get_assignment_with_key(s, "host-1")
            assert a1["sha256"] == "SHA-ASG" and a1["project_id"] == "proj-x"
            assert a1["scrub"] is False
            assert await repo.get_assignment_with_key(s, "absent-host") is None
        # scrub host-1: key_id cleared, scrub flag set
        async with s.begin():
            await repo.scrub(s, "host-1")
        async with s.begin():
            a1 = await repo.get_assignment_with_key(s, "host-1")
            assert a1["scrub"] is True and a1["key_id"] is None
        # unassign host-2: row gone -> lookup None
        async with s.begin():
            assert await repo.unassign(s, "host-2") is True
        async with s.begin():
            assert await repo.get_assignment_with_key(s, "host-2") is None
```

- [ ] **Step 2: Run it — FAIL** (functions missing).
- [ ] **Step 3: Implement — append to `app/repositories/sa_keys.py`:**

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert


async def assign(session: AsyncSession, hostname: str, key_id: UUID) -> None:
    stmt = pg_insert(SAKeyAssignment).values(
        hostname=hostname, key_id=key_id, scrub_requested_at=None, updated_at=_utcnow(),
    ).on_conflict_do_update(
        index_elements=["hostname"],
        set_={"key_id": key_id, "scrub_requested_at": None, "updated_at": _utcnow()},
    )
    await session.execute(stmt)


async def unassign(session: AsyncSession, hostname: str) -> bool:
    res = await session.execute(
        sa_delete(SAKeyAssignment).where(SAKeyAssignment.hostname == hostname)
    )
    return (res.rowcount or 0) > 0


async def scrub(session: AsyncSession, hostname: str) -> None:
    stmt = pg_insert(SAKeyAssignment).values(
        hostname=hostname, key_id=None, scrub_requested_at=_utcnow(), updated_at=_utcnow(),
    ).on_conflict_do_update(
        index_elements=["hostname"],
        set_={"key_id": None, "scrub_requested_at": _utcnow(), "updated_at": _utcnow()},
    )
    await session.execute(stmt)


async def get_assignment_with_key(session: AsyncSession, hostname: str) -> dict | None:
    row = (await session.execute(
        select(SAKeyAssignment, SAKey)
        .outerjoin(SAKey, SAKeyAssignment.key_id == SAKey.id)
        .where(SAKeyAssignment.hostname == hostname)
    )).first()
    if row is None:
        return None
    asg, key = row
    return {
        "key_id": asg.key_id,
        "sha256": key.sha256 if key is not None else None,
        "project_id": key.project_id if key is not None else None,
        "scrub": asg.scrub_requested_at is not None,
    }


async def list_assignments(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(
        select(SAKeyAssignment, SAKey)
        .outerjoin(SAKey, SAKeyAssignment.key_id == SAKey.id)
        .order_by(SAKeyAssignment.hostname)
    )).all()
    return [
        {
            "hostname": asg.hostname,
            "key_id": asg.key_id,
            "project_id": key.project_id if key is not None else None,
            "label": key.label if key is not None else None,
            "scrub": asg.scrub_requested_at is not None,
        }
        for asg, key in rows
    ]
```

- [ ] **Step 4: Run both repo tests** (uncomment Task 4's guarded block now that `assign`/`unassign` exist):

```bash
createdb -U macmini5 edu_sak_t5
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_sak_t5 RUN_DB_INTEGRATION=1 \
  uv run --extra dev alembic upgrade head
DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_sak_t5 RUN_DB_INTEGRATION=1 \
  uv run python -m pytest tests/integration/test_sa_keys_repo_assign.py tests/integration/test_sa_keys_repo_pool.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/sa_keys.py tests/integration/test_sa_keys_repo_assign.py tests/integration/test_sa_keys_repo_pool.py
git commit -m "feat(sa-keys): assignment repo (assign/unassign/scrub/lookup) + shared-key"
```

---

## Task 6: Header-only auth dependency (T-AUTH)

**Files:**
- Modify: `app/auth.py`
- Test: `tests/test_auth_strict.py`

**Interfaces:**
- Produces: `async def get_current_user_strict(authorization: Optional[str]) -> dict` — Bearer-header-only (no `?token=`), raises **503** when `valid_auth_tokens()` is empty, **401** on missing/invalid header.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_strict.py
import pytest
from fastapi import HTTPException
import app.auth as auth


@pytest.mark.asyncio
async def test_strict_requires_header_and_refuses_when_open(monkeypatch):
    # auth disabled -> refuse (a key vault must never be open)
    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: set())
    with pytest.raises(HTTPException) as e:
        await auth.get_current_user_strict(authorization="Bearer anything")
    assert e.value.status_code == 503

    monkeypatch.setattr(auth, "valid_auth_tokens", lambda: {"secret"})
    # valid header passes
    assert (await auth.get_current_user_strict(authorization="Bearer secret"))["auth"] == "token"
    # missing header -> 401
    with pytest.raises(HTTPException) as e:
        await auth.get_current_user_strict(authorization=None)
    assert e.value.status_code == 401
    # wrong token -> 401
    with pytest.raises(HTTPException) as e:
        await auth.get_current_user_strict(authorization="Bearer nope")
    assert e.value.status_code == 401
```

- [ ] **Step 2: Run it — FAIL.**
- [ ] **Step 3: Implement — append to `app/auth.py`:**

```python
async def get_current_user_strict(
    authorization: Optional[str] = Header(default=None),
) -> dict:
    """Header-only auth for serving credential bytes. Unlike get_current_user
    this rejects the ?token= query param (which would leak the token into
    proxy/access logs) and refuses entirely (503) when auth is disabled — a
    service-account-key vault must never be served wide-open."""
    valid = valid_auth_tokens()
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SA-key download requires AUTH_TOKEN to be configured",
        )
    provided: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(None, 1)[1].strip()
    if not provided or provided not in valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid auth token",
            headers={"WWW-Authenticate": 'Bearer realm="api"'},
        )
    return {"user_id": "authenticated", "auth": "token"}
```

- [ ] **Step 4: Run it — PASS.**

Run: `uv run python -m pytest tests/test_auth_strict.py -q`

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth_strict.py
git commit -m "feat(sa-keys): header-only get_current_user_strict (refuse-when-open)"
```

---

## Task 7: API — upload / list / delete

**Files:**
- Create: `app/api/v1/sa_keys.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/api/test_sa_keys_api.py`

**Interfaces:**
- Consumes: `sa_keys` repo, `sa_key_validate`, `storage`, `get_current_user`, `get_current_user_strict`.
- Produces routes under prefix `/sa-keys`: `POST ""`, `GET ""`, `DELETE "/{key_id}"`, `GET "/{key_id}/download"` (Task 8), assignment routes (Task 9). Router var name `router`.

- [ ] **Step 1: Write the failing test** (uses FastAPI TestClient with auth disabled; DB-gated for persistence)

```python
# tests/api/test_sa_keys_api.py
import os
import json
import pytest
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


def _good_key(project="proj-api"):
    return json.dumps({
        "type": "service_account", "project_id": project,
        "client_email": f"svc@{project}.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
    }).encode()


@pytest.mark.asyncio
async def test_upload_validates_dedups_and_lists_without_private_key(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "auth_token", "")  # auth disabled for the test
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        # reject a non-SA file
        r = await c.post("/api/v1/sa-keys", files={"file": ("bad.json", b"{}", "application/json")})
        assert r.status_code == 422
        # accept a good key, project auto-extracted
        r = await c.post("/api/v1/sa-keys", files={"file": ("k.json", _good_key(), "application/json")})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["project_id"] == "proj-api" and "private_key" not in body
        kid = body["id"]
        # the bytes landed on disk
        import app.services.storage as storage
        assert storage.sa_key_path(kid).exists()
        # re-upload identical bytes dedups to the same id
        r2 = await c.post("/api/v1/sa-keys", files={"file": ("k.json", _good_key(), "application/json")})
        assert r2.json()["id"] == kid
        # list never leaks private_key
        r = await c.get("/api/v1/sa-keys")
        assert all("private_key" not in k for k in r.json()["keys"])
        # delete works (unassigned)
        assert (await c.delete(f"/api/v1/sa-keys/{kid}")).status_code == 200
```

- [ ] **Step 2: Run it — FAIL** (404, router not mounted).
- [ ] **Step 3: Implement the router**

```python
# app/api/v1/sa_keys.py
from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_strict
from app.db import get_session
from app.repositories import sa_keys as repo
from app.services import storage
from app.services.sa_key_validate import InvalidServiceAccountKey, parse_and_validate_sa_key

router = APIRouter(prefix="/sa-keys", tags=["sa-keys"])


def _meta(row) -> dict:
    return {
        "id": str(row.id), "project_id": row.project_id,
        "client_email": row.client_email, "original_filename": row.original_filename,
        "label": row.label, "byte_size": row.byte_size,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("", status_code=201)
async def upload_sa_key(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    body = await file.read()
    try:
        project_id, client_email = parse_and_validate_sa_key(body)
    except InvalidServiceAccountKey as exc:
        raise HTTPException(422, f"not a valid service-account key: {exc}")
    sha = hashlib.sha256(body).hexdigest()
    row = await repo.create_or_get(
        session, original_filename=file.filename or "key.json",
        project_id=project_id, client_email=client_email, sha256=sha, byte_size=len(body),
    )
    await session.commit()
    path = storage.sa_key_path(row.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)  # idempotent on dedup (same bytes)
    return _meta(row)


@router.get("")
async def list_sa_keys(session: AsyncSession = Depends(get_session)) -> dict:
    keys = await repo.list_keys(session)
    for k in keys:
        k["id"] = str(k["id"])
        k["created_at"] = k["created_at"].isoformat() if k["created_at"] else None
    return {"keys": keys}


@router.delete("/{key_id}")
async def delete_sa_key(key_id: UUID, session: AsyncSession = Depends(get_session)) -> dict:
    outcome = await repo.delete(session, key_id)
    if outcome == "not_found":
        raise HTTPException(404, "no such key")
    if outcome == "assigned":
        raise HTTPException(409, "key is still assigned to a worker; unassign first")
    await session.commit()
    storage.sa_key_path(key_id).unlink(missing_ok=True)
    return {"deleted": str(key_id)}
```

- [ ] **Step 4: Mount the router** — in `app/api/v1/__init__.py`, mirroring the existing lines:

```python
from app.api.v1 import sa_keys  # add to the import group
api_v1_router.include_router(sa_keys.router, dependencies=[Depends(get_current_user)])
```

(The download route in Task 8 overrides this router-level dep with `get_current_user_strict` at the route level.)

- [ ] **Step 5: Run it — PASS** (scratch DB, same recipe as Task 4 with `edu_sak_t7`).
- [ ] **Step 6: Commit**

```bash
git add app/api/v1/sa_keys.py app/api/v1/__init__.py tests/api/test_sa_keys_api.py
git commit -m "feat(sa-keys): upload/list/delete API (validate, dedup, no private_key leak)"
```

---

## Task 8: API — download (header-only, refuse-when-open) (T-AUTH)

**Files:**
- Modify: `app/api/v1/sa_keys.py`
- Test: `tests/api/test_sa_keys_download.py`

**Interfaces:**
- Consumes: `get_current_user_strict`, `storage.sa_key_path`, `sa_keys` repo `get`.
- Produces: `GET /sa-keys/{key_id}/download` → raw JSON bytes, header-only auth.

- [ ] **Step 1: Write the failing test** (auth ENABLED so the strict gate bites)

```python
# tests/api/test_sa_keys_download.py
import os
import json
import pytest
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


def _good_key():
    return json.dumps({
        "type": "service_account", "project_id": "dl-proj",
        "client_email": "svc@dl-proj.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
    }).encode()


@pytest.mark.asyncio
async def test_download_is_header_only(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "auth_token", "secret")
    from main import app
    H = {"Authorization": "Bearer secret"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        kid = (await c.post("/api/v1/sa-keys",
               files={"file": ("k.json", _good_key(), "application/json")}, headers=H)).json()["id"]
        # ?token= works on a normal endpoint (proves it's not globally broken)
        assert (await c.get("/api/v1/sa-keys?token=secret")).status_code == 200
        # ?token= is REJECTED on the download route (header-only)
        assert (await c.get(f"/api/v1/sa-keys/{kid}/download?token=secret")).status_code == 401
        # correct header serves the bytes
        r = await c.get(f"/api/v1/sa-keys/{kid}/download", headers=H)
        assert r.status_code == 200 and json.loads(r.content)["project_id"] == "dl-proj"
        # missing header -> 401
        assert (await c.get(f"/api/v1/sa-keys/{kid}/download")).status_code == 401
```

- [ ] **Step 2: Run it — FAIL** (route missing).
- [ ] **Step 3: Implement — append to `app/api/v1/sa_keys.py`:**

```python
from fastapi import Response


@router.get("/{key_id}/download")
async def download_sa_key(
    key_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user_strict),  # header-only; overrides router dep
) -> Response:
    row = await repo.get(session, key_id)
    if row is None:
        raise HTTPException(404, "no such key")
    path = storage.sa_key_path(key_id)
    if not path.exists():
        raise HTTPException(404, "key bytes missing on disk")
    return Response(content=path.read_bytes(), media_type="application/json")
```

Note: the route-level `Depends(get_current_user_strict)` runs IN ADDITION to the router-level `get_current_user`. Because `get_current_user` accepts `?token=` it would pass a `?token=` request, but `get_current_user_strict` then rejects it → 401, which is the desired behavior. Both run; the stricter one wins.

- [ ] **Step 4: Run it — PASS** (scratch DB `edu_sak_t8`).
- [ ] **Step 5: Commit**

```bash
git add app/api/v1/sa_keys.py tests/api/test_sa_keys_download.py
git commit -m "feat(sa-keys): header-only key download (rejects ?token=, refuses when open)"
```

---

## Task 9: API — assign / unassign / scrub + workers-list augmentation

**Files:**
- Modify: `app/api/v1/sa_keys.py`
- Modify: `app/api/v1/workers.py`
- Test: `tests/api/test_sa_keys_assign_api.py`

**Interfaces:**
- Consumes: `sa_keys` repo assign/unassign/scrub/list_assignments.
- Produces: `PUT /sa-keys/assignments/{hostname}` `{key_id}`; `DELETE /sa-keys/assignments/{hostname}`; `POST /sa-keys/assignments/{hostname}/scrub`; `GET /sa-keys/assignments`. The `GET /workers` response gains `"assignments": [...]` from `list_assignments`.

- [ ] **Step 1: Write the failing test** (DB-gated, auth disabled)

```python
# tests/api/test_sa_keys_assign_api.py
import os
import json
import pytest
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


def _good_key():
    return json.dumps({
        "type": "service_account", "project_id": "asg-proj",
        "client_email": "svc@asg-proj.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
    }).encode()


@pytest.mark.asyncio
async def test_assign_unassign_scrub(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "auth_token", "")
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        kid = (await c.post("/api/v1/sa-keys",
               files={"file": ("k.json", _good_key(), "application/json")})).json()["id"]
        assert (await c.put(f"/api/v1/sa-keys/assignments/host-z",
                            json={"key_id": kid})).status_code == 200
        got = (await c.get("/api/v1/sa-keys/assignments")).json()["assignments"]
        assert any(a["hostname"] == "host-z" and a["project_id"] == "asg-proj" for a in got)
        assert (await c.post(f"/api/v1/sa-keys/assignments/host-z/scrub")).status_code == 200
        assert (await c.delete(f"/api/v1/sa-keys/assignments/host-z")).status_code == 200
        # workers endpoint surfaces assignments
        assert "assignments" in (await c.get("/api/v1/workers")).json()
```

- [ ] **Step 2: Run it — FAIL.**
- [ ] **Step 3: Implement — append to `app/api/v1/sa_keys.py`:**

```python
from pydantic import BaseModel


class AssignRequest(BaseModel):
    key_id: UUID


@router.get("/assignments")
async def list_assignments(session: AsyncSession = Depends(get_session)) -> dict:
    rows = await repo.list_assignments(session)
    for r in rows:
        r["key_id"] = str(r["key_id"]) if r["key_id"] else None
    return {"assignments": rows}


@router.put("/assignments/{hostname}")
async def assign_sa_key(
    hostname: str, req: AssignRequest, session: AsyncSession = Depends(get_session),
) -> dict:
    if await repo.get(session, req.key_id) is None:
        raise HTTPException(404, "no such key")
    await repo.assign(session, hostname, req.key_id)
    await session.commit()
    return {"hostname": hostname, "key_id": str(req.key_id)}


@router.delete("/assignments/{hostname}")
async def unassign_sa_key(hostname: str, session: AsyncSession = Depends(get_session)) -> dict:
    await repo.unassign(session, hostname)
    await session.commit()
    return {"hostname": hostname, "unassigned": True}


@router.post("/assignments/{hostname}/scrub")
async def scrub_sa_key(hostname: str, session: AsyncSession = Depends(get_session)) -> dict:
    await repo.scrub(session, hostname)
    await session.commit()
    return {"hostname": hostname, "scrub": True}
```

- [ ] **Step 4: Augment the workers endpoint** — in `app/api/v1/workers.py`, add the import and one field. Replace the `list_workers` body's return with:

```python
from app.repositories import sa_keys as sa_keys_repo  # add at top

# inside list_workers, before the return:
    assignments = await sa_keys_repo.list_assignments(session)
    for a in assignments:
        a["key_id"] = str(a["key_id"]) if a["key_id"] else None
    return {
        "workers": rows,
        "total": len(rows),
        "online": online,
        "stale_after_seconds": settings.worker_registry_stale_seconds,
        "assignments": assignments,
    }
```

- [ ] **Step 5: Run it — PASS** (scratch DB `edu_sak_t9`).
- [ ] **Step 6: Commit**

```bash
git add app/api/v1/sa_keys.py app/api/v1/workers.py tests/api/test_sa_keys_assign_api.py
git commit -m "feat(sa-keys): assign/unassign/scrub API + workers-list assignment view"
```

---

## Task 10: Worker apply — `.env` upsert (UTF-8, line-preserving)

**Files:**
- Create: `app/services/sa_key_apply.py`
- Test: `tests/services/test_sa_key_apply_env.py`

**Interfaces:**
- Produces: `upsert_env_file(env_path: Path, updates: dict[str, str | None]) -> None` — set each key (value `None` ⇒ delete that key's line), preserve every other line byte-for-byte, UTF-8.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_sa_key_apply_env.py
from pathlib import Path
from app.services.sa_key_apply import upsert_env_file


def test_upsert_preserves_non_ascii_and_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "AUTH_TOKEN=123\n"
        "NOTION_SUBJECT_PAGES=ru:Математика|5=abc\n"   # Cyrillic must survive
        "GOOGLE_CLOUD_PROJECT=old-proj\n",
        encoding="utf-8",
    )
    upsert_env_file(env, {
        "GOOGLE_APPLICATION_CREDENTIALS": "/abs/active.json",
        "GOOGLE_CLOUD_PROJECT": "new-proj",
    })
    out = env.read_text(encoding="utf-8")
    assert "NOTION_SUBJECT_PAGES=ru:Математика|5=abc" in out  # untouched, non-ASCII intact
    assert "AUTH_TOKEN=123" in out
    assert "GOOGLE_CLOUD_PROJECT=new-proj" in out and "old-proj" not in out  # replaced in place
    assert "GOOGLE_APPLICATION_CREDENTIALS=/abs/active.json" in out  # appended

    # removal: value None drops the line
    upsert_env_file(env, {"GOOGLE_APPLICATION_CREDENTIALS": None, "GOOGLE_CLOUD_PROJECT": None})
    out = env.read_text(encoding="utf-8")
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in out
    assert "GOOGLE_CLOUD_PROJECT" not in out
    assert "NOTION_SUBJECT_PAGES=ru:Математика|5=abc" in out  # still intact
```

- [ ] **Step 2: Run it — FAIL.**
- [ ] **Step 3: Implement** (create `app/services/sa_key_apply.py` with this function; the rest of the module lands in Task 11):

```python
# app/services/sa_key_apply.py
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
```

- [ ] **Step 4: Run it — PASS.**

Run: `uv run python -m pytest tests/services/test_sa_key_apply_env.py -q`

- [ ] **Step 5: Commit**

```bash
git add app/services/sa_key_apply.py tests/services/test_sa_key_apply_env.py
git commit -m "feat(sa-keys): UTF-8 line-preserving .env upsert"
```

---

## Task 11: Worker apply — atomic write + paired env + capability rebind (T-ATOMIC + T-CAP)

**Files:**
- Modify: `app/services/sa_key_apply.py`
- Modify: `app/services/worker.py`
- Test: `tests/services/test_sa_key_apply_core.py`, `tests/services/test_worker_capability_rebind.py`

**Interfaces:**
- Consumes: `storage.sa_key_active_path`, `worker._compute_capabilities`, `worker._capability_blob`.
- Produces (in `sa_key_apply.py`):
  - `write_active_key(key_bytes: bytes, dest: Path) -> None` — temp file in the same dir + `os.replace` (atomic).
  - `set_credentials_env(env: MutableMapping, creds_path: str, project_id: str) -> None` — set both vars.
  - `clear_credentials_env(env: MutableMapping) -> None` — pop both.
  - `pull_key_bytes(key_id: str) -> bytes` — read local disk if `fleet_head_url` empty, else httpx GET the download endpoint with Bearer (Task 12 wires this into sync, but implement here).
- Produces (in `worker.py`): `_rebind_capabilities() -> None` — reassign module globals `CAPABILITIES` and `CAPABILITY_BLOB` from `os.environ`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_sa_key_apply_core.py
from pathlib import Path
from app.services.sa_key_apply import write_active_key, set_credentials_env, clear_credentials_env


def test_write_active_key_is_atomic_no_temp_left(tmp_path):
    dest = tmp_path / "sa_keys" / "active.json"
    dest.parent.mkdir(parents=True)
    write_active_key(b'{"k":1}', dest)
    assert dest.read_bytes() == b'{"k":1}'
    # no .tmp residue beside it
    assert [p.name for p in dest.parent.iterdir()] == ["active.json"]
    # overwrite in place
    write_active_key(b'{"k":2}', dest)
    assert dest.read_bytes() == b'{"k":2}'
    assert [p.name for p in dest.parent.iterdir()] == ["active.json"]


def test_credentials_env_paired():
    env = {}
    set_credentials_env(env, "/abs/active.json", "proj-1")
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == "/abs/active.json"
    assert env["GOOGLE_CLOUD_PROJECT"] == "proj-1"
    clear_credentials_env(env)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert "GOOGLE_CLOUD_PROJECT" not in env
```

```python
# tests/services/test_worker_capability_rebind.py
import importlib
import app.services.worker as worker


def test_rebind_flips_gemini_capability(monkeypatch):
    # start keyless: no gemini creds in env
    for k in ("GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    worker.CAPABILITIES = worker._compute_capabilities(__import__("os").environ)
    worker.CAPABILITY_BLOB = worker._capability_blob(__import__("os").environ)
    assert worker.CAPABILITIES["can_gemini_api"] is False
    assert worker.CAPABILITY_BLOB["api"]["gemini"] is False

    # apply Vertex creds live, then rebind
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/abs/active.json")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-x")
    worker._rebind_capabilities()
    assert worker.CAPABILITIES["can_gemini_api"] is True
    assert worker.CAPABILITY_BLOB["api"]["gemini"] is True
```

- [ ] **Step 2: Run them — FAIL** (`write_active_key` / `_rebind_capabilities` missing).
- [ ] **Step 3: Implement — append to `app/services/sa_key_apply.py`:**

```python
import os
from typing import MutableMapping
from uuid import uuid4

import httpx

from app.config import settings
from app.services import storage

_PULL_TIMEOUT = 30.0


def write_active_key(key_bytes: bytes, dest: Path) -> None:
    """Atomically place `key_bytes` at `dest` (same-dir temp + os.replace), so a
    concurrent reader (an agent spawn assembling child_env) never sees a torn file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f"active.json.{os.getpid()}.{uuid4().hex}.tmp"
    tmp.write_bytes(key_bytes)
    os.replace(tmp, dest)  # atomic on same filesystem


def set_credentials_env(env: MutableMapping, creds_path: str, project_id: str) -> None:
    env["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
    env["GOOGLE_CLOUD_PROJECT"] = project_id


def clear_credentials_env(env: MutableMapping) -> None:
    env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    env.pop("GOOGLE_CLOUD_PROJECT", None)


def pull_key_bytes(key_id: str) -> bytes:
    """Read the key bytes: straight from disk on the head (no fleet_head_url), else
    HTTP GET the head's download endpoint with the Bearer token (book_fetch idiom)."""
    head = settings.fleet_head_url.strip()
    if not head:
        return storage.sa_key_path(key_id).read_bytes()
    token = settings.auth_token.split(",")[0].strip()
    url = f"{head.rstrip('/')}/api/v1/sa-keys/{key_id}/download"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=_PULL_TIMEOUT) as http:
        resp = http.get(url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"head returned HTTP {resp.status_code}")
        if not resp.content:
            raise RuntimeError("head returned empty body")
        return resp.content
```

- [ ] **Step 4: Implement `_rebind_capabilities` in `app/services/worker.py`** — add directly after the `CAPABILITY_BLOB` definition (after line 99):

```python
def _rebind_capabilities() -> None:
    """Recompute the frozen capability globals from the CURRENT os.environ after a
    live SA-key apply/scrub. The claim gate reads CAPABILITIES at call time
    (worker.py _claim_one) and the heartbeat publishes CAPABILITY_BLOB, so
    reassigning the module globals is what makes a freshly-keyed worker start
    claiming gemini-api jobs without a restart."""
    global CAPABILITIES, CAPABILITY_BLOB
    CAPABILITIES = _compute_capabilities(os.environ)
    CAPABILITY_BLOB = _capability_blob(os.environ)
```

- [ ] **Step 5: Run both tests — PASS.**

Run: `uv run python -m pytest tests/services/test_sa_key_apply_core.py tests/services/test_worker_capability_rebind.py -q`

- [ ] **Step 6: Commit**

```bash
git add app/services/sa_key_apply.py app/services/worker.py tests/services/test_sa_key_apply_core.py tests/services/test_worker_capability_rebind.py
git commit -m "feat(sa-keys): atomic key write, paired creds env, capability rebind"
```

---

## Task 12: Worker sync orchestration (apply / scrub / change-detection, idle-gated)

**Files:**
- Modify: `app/services/worker.py`
- Test: `tests/services/test_worker_sa_key_sync.py`

**Interfaces:**
- Consumes: `sa_keys_repo.get_assignment_with_key`, all `sa_key_apply` functions, `_rebind_capabilities`, `storage.sa_key_active_path`.
- Produces: `Worker.hostname` (bare hostname), `Worker._applied_key_sha: str | None`, `Worker._last_key_sync_at: float`, and `async def Worker._sync_sa_key(self) -> None`.

- [ ] **Step 1: Add fields** — in `Worker.__init__` (after `self.id = _worker_id()` at `worker.py:177`), add:

```python
        self.hostname = socket.gethostname()
        self._applied_key_sha: str | None = None
        self._last_key_sync_at = 0.0
```

And add the imports at the top of `worker.py` (with the other `app.services` imports near line 49):

```python
from pathlib import Path
from app.repositories import sa_keys as sa_keys_repo
from app.services import sa_key_apply
from app.services.storage import sa_key_active_path
```

- [ ] **Step 2: Write the failing test**

```python
# tests/services/test_worker_sa_key_sync.py
import os
import pytest
import app.services.worker as worker
import app.services.sa_key_apply as apply_mod


class _FakeSession:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


@pytest.mark.asyncio
async def test_sync_applies_when_idle_and_noops_when_unchanged(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    worker.CAPABILITIES = worker._compute_capabilities(os.environ)

    w = worker.Worker(concurrency=1)
    w._tasks = set()  # idle

    monkeypatch.setattr(worker, "SessionLocal", lambda: _FakeSession())
    async def fake_lookup(session, hostname):
        return {"key_id": "11111111-1111-1111-1111-111111111111",
                "sha256": "SHA-NEW", "project_id": "proj-live", "scrub": False}
    monkeypatch.setattr(worker.sa_keys_repo, "get_assignment_with_key", fake_lookup)
    monkeypatch.setattr(apply_mod, "pull_key_bytes", lambda kid: b'{"type":"service_account"}')
    monkeypatch.setattr(worker, "Path", __import__("pathlib").Path)

    # point .env at a temp file so we don't touch the repo's
    envfile = tmp_path / ".env"
    monkeypatch.setattr(worker, "_WORKER_ENV_PATH", envfile, raising=False)

    await w._sync_sa_key()
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == "proj-live"
    assert worker.CAPABILITIES["can_gemini_api"] is True
    assert w._applied_key_sha == "SHA-NEW"
    assert sa_key_active_path_exists(tmp_path)

    # second sync, unchanged sha -> no re-pull (pull would raise if called)
    monkeypatch.setattr(apply_mod, "pull_key_bytes",
                        lambda kid: (_ for _ in ()).throw(AssertionError("should not pull")))
    w._last_key_sync_at = 0.0
    await w._sync_sa_key()  # must be a no-op


def sa_key_active_path_exists(tmp_path):
    return (tmp_path / "sa_keys" / "active.json").exists()
```

(If `from app.services.storage import sa_key_active_path` is used directly in the test, import it; the helper above checks the path instead to avoid settings caching.)

- [ ] **Step 3: Implement `_sync_sa_key`** — add as a `Worker` method in `worker.py`. Use a module-level `_WORKER_ENV_PATH = Path(".env")` near the top so tests can override it:

```python
# near module top, after imports
_WORKER_ENV_PATH = Path(".env")  # worker's project-root .env (load_dotenv default)
```

```python
    async def _sync_sa_key(self) -> None:
        """Resolve this host's SA-key assignment and apply/scrub it LIVE when it
        changed. Idle-gated: the os.environ swap runs only when no job is in
        flight (len(self._tasks)==0), so no concurrent agent spawn snapshots a
        torn credential state. Best-effort: any failure is logged, never fatal."""
        try:
            async with SessionLocal() as session:
                asg = await sa_keys_repo.get_assignment_with_key(session, self.hostname)
        except Exception:
            logger.warning(f"worker {self.id} sa-key assignment read failed")
            return
        if asg is None:
            return  # non-destructive: keep whatever is currently applied

        # Scrub: actively clear this host's key (the revoke path).
        if asg["scrub"]:
            if self._applied_key_sha is not None:
                if self._tasks:
                    return  # defer the clear until idle
                sa_key_apply.clear_credentials_env(os.environ)
                sa_key_apply.upsert_env_file(
                    _WORKER_ENV_PATH,
                    {"GOOGLE_APPLICATION_CREDENTIALS": None, "GOOGLE_CLOUD_PROJECT": None},
                )
                sa_key_active_path().unlink(missing_ok=True)
                _rebind_capabilities()
                self._applied_key_sha = None
                logger.warning(f"worker {self.id} SA key SCRUBBED (revoked)")
            return

        if asg["sha256"] == self._applied_key_sha:
            return  # unchanged — fast no-op
        if self._tasks:
            return  # in-flight jobs: defer the swap to the next idle moment

        try:
            key_bytes = await asyncio.to_thread(sa_key_apply.pull_key_bytes, str(asg["key_id"]))
            dest = sa_key_active_path()
            sa_key_apply.write_active_key(key_bytes, dest)
            creds_path = str(dest.resolve())
            sa_key_apply.set_credentials_env(os.environ, creds_path, asg["project_id"])
            sa_key_apply.upsert_env_file(
                _WORKER_ENV_PATH,
                {"GOOGLE_APPLICATION_CREDENTIALS": creds_path, "GOOGLE_CLOUD_PROJECT": asg["project_id"]},
            )
            _rebind_capabilities()
            self._applied_key_sha = asg["sha256"]
            logger.info(
                f"worker {self.id} applied SA key project={asg['project_id']} "
                f"(live, no restart) — gemini_api={CAPABILITIES['can_gemini_api']}"
            )
        except Exception:
            logger.exception(f"worker {self.id} SA key apply failed")
```

- [ ] **Step 4: Run the test — PASS.**

Run: `uv run python -m pytest tests/services/test_worker_sa_key_sync.py -q`

- [ ] **Step 5: Commit**

```bash
git add app/services/worker.py tests/services/test_worker_sa_key_sync.py
git commit -m "feat(sa-keys): idle-gated worker sync (apply/scrub/change-detection)"
```

---

## Task 13: Worker integration — startup-before-claim + main-loop hook

**Files:**
- Modify: `app/services/worker.py`
- Test: `tests/services/test_worker_startup_applies_key.py`

**Interfaces:**
- Consumes: `Worker._sync_sa_key`.
- Produces: a startup sync call before the claim loop, and a throttled main-loop sync. No new public surface.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_worker_startup_applies_key.py
import pytest
import app.services.worker as worker


@pytest.mark.asyncio
async def test_run_syncs_key_before_claiming(monkeypatch):
    calls = []
    w = worker.Worker(concurrency=1)

    async def fake_sync():
        calls.append(("sync", len(calls)))
    async def fake_sweep():
        calls.append(("sweep", len(calls)))
    async def fake_claim():
        # stop after the first claim attempt so run() exits
        w.stop()
        return None

    monkeypatch.setattr(w, "_sync_sa_key", fake_sync)
    monkeypatch.setattr(w, "_sweep_stuck_jobs", fake_sweep)
    monkeypatch.setattr(w, "_claim_one", fake_claim)
    # neuter the registry heartbeat loop so the test stays in-process
    async def noop():
        return
    monkeypatch.setattr(w, "_registry_heartbeat_loop", noop)

    await w.run()
    # a sync happened before the first claim attempt
    assert "sync" in [c[0] for c in calls]
    sync_idx = next(i for i, c in enumerate(calls) if c[0] == "sync")
    claim_present = any(c[0] == "sweep" for c in calls)
    assert sync_idx is not None and claim_present
```

- [ ] **Step 2: Run it — FAIL** (no startup sync yet).
- [ ] **Step 3: Implement** — in `Worker.run()`, add a startup sync after the startup sweep (`worker.py:214`, after `await self._sweep_stuck_jobs()`):

```python
        # Apply this host's assigned SA key (if any) BEFORE the claim loop, so a
        # keyless boot that has an assignment gains gemini-api capability before
        # it ever tries to claim. Idle by construction here (no jobs yet).
        await self._sync_sa_key()
```

And inside the main loop, alongside the throttled sweep/budget block (after `worker.py:231`), add a throttled key sync:

```python
                if now - self._last_key_sync_at > settings.heartbeat_seconds:
                    await self._sync_sa_key()
                    self._last_key_sync_at = now
```

- [ ] **Step 4: Run it — PASS.**
- [ ] **Step 5: Run the whole worker + sa-key suite** to confirm no regression:

```bash
uv run python -m pytest tests/services/test_worker_sa_key_sync.py tests/services/test_worker_startup_applies_key.py tests/services/test_worker_capability_rebind.py tests/services/test_sa_key_apply_core.py tests/services/test_sa_key_apply_env.py tests/services/test_sa_key_validate.py tests/services/test_storage_sa_keys.py -q
```

- [ ] **Step 6: Commit**

```bash
git add app/services/worker.py tests/services/test_worker_startup_applies_key.py
git commit -m "feat(sa-keys): worker applies assigned key at startup + each loop"
```

---

## Task 14: Frontend — API client + types

**Files:**
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/lib/types.ts`
- Test: typecheck (`tsc`) + build only.

**Interfaces:**
- Produces (on the `api` object): `listSaKeys()`, `uploadSaKey(file)`, `deleteSaKey(id)`, `listSaKeyAssignments()`, `assignSaKey(hostname, keyId)`, `unassignSaKey(hostname)`, `scrubSaKey(hostname)`. Types `SaKey`, `SaKeyAssignment`.

- [ ] **Step 1: Add types** — append to `web/src/lib/types.ts`:

```typescript
export interface SaKey {
  id: string;
  project_id: string;
  client_email: string;
  original_filename: string;
  label: string | null;
  byte_size: number;
  created_at: string | null;
  worker_count: number;
}

export interface SaKeyAssignment {
  hostname: string;
  key_id: string | null;
  project_id: string | null;
  label: string | null;
  scrub: boolean;
}
```

- [ ] **Step 2: Add client functions** — inside the `api` object in `web/src/lib/api.ts` (follow the existing `authFetch` / `listWorkers` idiom at api.ts:428):

```typescript
  async listSaKeys(): Promise<{ keys: SaKey[] }> {
    const res = await authFetch("/api/v1/sa-keys");
    return res.json();
  },
  async uploadSaKey(file: File): Promise<SaKey> {
    const form = new FormData();
    form.append("file", file);
    const res = await authFetch("/api/v1/sa-keys", { method: "POST", body: form });
    if (!res.ok) throw new Error((await res.json()).detail ?? "upload failed");
    return res.json();
  },
  async deleteSaKey(id: string): Promise<void> {
    const res = await authFetch(`/api/v1/sa-keys/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok) throw new Error((await res.json()).detail ?? "delete failed");
  },
  async listSaKeyAssignments(): Promise<{ assignments: SaKeyAssignment[] }> {
    const res = await authFetch("/api/v1/sa-keys/assignments");
    return res.json();
  },
  async assignSaKey(hostname: string, keyId: string): Promise<void> {
    const res = await authFetch(`/api/v1/sa-keys/assignments/${encodeURIComponent(hostname)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key_id: keyId }),
    });
    if (!res.ok) throw new Error((await res.json()).detail ?? "assign failed");
  },
  async unassignSaKey(hostname: string): Promise<void> {
    await authFetch(`/api/v1/sa-keys/assignments/${encodeURIComponent(hostname)}`, { method: "DELETE" });
  },
  async scrubSaKey(hostname: string): Promise<void> {
    await authFetch(`/api/v1/sa-keys/assignments/${encodeURIComponent(hostname)}/scrub`, { method: "POST" });
  },
```

Add `SaKey, SaKeyAssignment` to the existing type import from `./types` at the top of `api.ts`. Confirm `authFetch` returns a `Response` (it does — used by `listWorkers`) and supports a `FormData` body without a forced JSON content-type (check the `authFetch` helper; if it hardcodes `Content-Type: application/json`, pass the upload through a path that omits it — follow how `uploadBook` posts a file in `api.ts`).

- [ ] **Step 3: Typecheck + build**

```bash
cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build
```
Expected: no type errors; build succeeds (pre-existing chunk-size warning is fine).

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/types.ts
git commit -m "feat(sa-keys): FE api client + types"
```

---

## Task 15: Frontend — Keys panel on the Fleet page

**Files:**
- Create: `web/src/components/fleet/sa-keys-panel.tsx`
- Modify: `web/src/routes/fleet.tsx`
- Test: `tsc` + build + manual eyeball.

**Interfaces:**
- Consumes: `api.listSaKeys`, `api.uploadSaKey`, `api.deleteSaKey`, `api.listWorkers`, `api.listSaKeyAssignments`, `api.assignSaKey`, `api.unassignSaKey`, `api.scrubSaKey`.
- Produces: `<SaKeysPanel />` mounted in `fleet.tsx`.

- [ ] **Step 1: Build the panel** — create `web/src/components/fleet/sa-keys-panel.tsx`. Follow the existing fleet component idiom (`worker-cards.tsx` for `useQuery`/`useMutation` + `queryClient.invalidateQueries`, `web/src/lib/ui.ts` for the styling kit). The panel renders:
  1. An upload control (`<input type="file" accept="application/json">` → `api.uploadSaKey`, invalidate `["sa-keys"]` on success, surface the 422 message on a bad key).
  2. The key pool: for each `SaKey`, show `project_id`, `client_email`, `worker_count`, and a delete button (disabled when `worker_count > 0`, tooltip "unassign first").
  3. A per-host table: derive each unique hostname from `listWorkers().workers` (`pc_id.split(":")[0]`, deduped) joined with `listSaKeyAssignments()`; each row has a `<select>` of keys (assign), an Unassign button, and a Scrub button.

Use these exact query keys so invalidation is consistent: `["sa-keys"]`, `["sa-key-assignments"]`, `["workers"]`.

```tsx
// web/src/components/fleet/sa-keys-panel.tsx
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../lib/api";

export function SaKeysPanel() {
  const qc = useQueryClient();
  const keysQ = useQuery({ queryKey: ["sa-keys"], queryFn: api.listSaKeys });
  const asgQ = useQuery({ queryKey: ["sa-key-assignments"], queryFn: api.listSaKeyAssignments });
  const workersQ = useQuery({ queryKey: ["workers"], queryFn: api.listWorkers });
  const [err, setErr] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: (f: File) => api.uploadSaKey(f),
    onError: (e: Error) => setErr(e.message),
    onSuccess: () => { setErr(null); qc.invalidateQueries({ queryKey: ["sa-keys"] }); },
  });
  const assign = useMutation({
    mutationFn: ({ host, key }: { host: string; key: string }) => api.assignSaKey(host, key),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sa-key-assignments"] });
                       qc.invalidateQueries({ queryKey: ["sa-keys"] }); },
  });
  const unassign = useMutation({
    mutationFn: (host: string) => api.unassignSaKey(host),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sa-key-assignments"] });
                       qc.invalidateQueries({ queryKey: ["sa-keys"] }); },
  });
  const scrub = useMutation({
    mutationFn: (host: string) => api.scrubSaKey(host),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sa-key-assignments"] }),
  });
  const del = useMutation({
    mutationFn: (id: string) => api.deleteSaKey(id),
    onError: (e: Error) => setErr(e.message),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sa-keys"] }),
  });

  const keys = keysQ.data?.keys ?? [];
  const assignments = asgQ.data?.assignments ?? [];
  const hosts = Array.from(new Set((workersQ.data?.workers ?? []).map(w => w.pc_id.split(":")[0]))).sort();
  const asgFor = (h: string) => assignments.find(a => a.hostname === h) ?? null;

  return (
    <section>
      <h2>Service-account keys</h2>
      {err && <p role="alert">{err}</p>}
      <input type="file" accept="application/json"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); }} />
      <ul>
        {keys.map(k => (
          <li key={k.id}>
            <code>{k.project_id}</code> · {k.client_email} · {k.worker_count} worker(s)
            <button disabled={k.worker_count > 0} onClick={() => del.mutate(k.id)}>Delete</button>
          </li>
        ))}
      </ul>
      <table>
        <thead><tr><th>Host</th><th>Assigned key</th><th>Actions</th></tr></thead>
        <tbody>
          {hosts.map(h => {
            const a = asgFor(h);
            return (
              <tr key={h}>
                <td>{h}</td>
                <td>{a?.project_id ?? (a?.scrub ? "(scrubbed)" : "—")}</td>
                <td>
                  <select defaultValue={a?.key_id ?? ""}
                    onChange={(e) => e.target.value && assign.mutate({ host: h, key: e.target.value })}>
                    <option value="">Assign key…</option>
                    {keys.map(k => <option key={k.id} value={k.id}>{k.project_id}</option>)}
                  </select>
                  <button onClick={() => unassign.mutate(h)}>Unassign</button>
                  <button onClick={() => scrub.mutate(h)}>Scrub</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
```

Apply the project's `ui.ts` classNames to match the surrounding fleet styling rather than raw HTML elements; keep the data wiring (query keys, mutations, invalidations) exactly as above.

- [ ] **Step 2: Mount it** — in `web/src/routes/fleet.tsx`, import and render `<SaKeysPanel />` in the fleet layout (near the worker cards section). Match the existing section placement/order.

- [ ] **Step 3: Typecheck + build**

```bash
cd web && npx tsc -p tsconfig.app.json --noEmit && npm run build
```
Expected: no type errors; build succeeds.

- [ ] **Step 4: Manual eyeball** — `npm run dev`, open the Fleet page: upload a key (bad JSON → inline error; good JSON → appears with project_id), assign it to a host, confirm the row reflects the assignment, Unassign/Scrub update the row. (The live worker-apply proof is the acceptance gate below, not this UI check.)

- [ ] **Step 5: Commit**

```bash
git add web/src/components/fleet/sa-keys-panel.tsx web/src/routes/fleet.tsx
git commit -m "feat(sa-keys): Fleet Keys panel — upload, pool, per-host assign/scrub"
```

---

## Acceptance gate (generation-affecting — run before finishing)

This feature changes whether a worker can serve gemini-api jobs, so prove it end-to-end, not from unit tests alone:

- [ ] **Live no-restart capability flip.** With a real (or sandbox) SA key:
  1. Start a worker process with **no** gemini creds in its env. Confirm its startup log shows `gemini side` missing and `CAPABILITIES["can_gemini_api"]` is False (it will not claim gemini-api jobs).
  2. Upload the key via `POST /api/v1/sa-keys`, then `PUT /api/v1/sa-keys/assignments/<that-host>` with the key id.
  3. Within ≤`heartbeat_seconds` the worker logs `applied SA key project=… (live, no restart) — gemini_api=True`, `var/sa_keys/active.json` exists, and the worker's `.env` now carries `GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT` with all prior lines intact.
  4. The worker now claims a gemini-api job and the spawn bills the assigned project (check an `agent_usages` row / the job runs without the `_auth_env` Vertex raise).
  5. `POST …/scrub` → the worker clears the key and `can_gemini_api` returns to False at the next idle sync.

---

## Finish (per CLAUDE.md — do not defer)

- [ ] Full suite green: `uv run python -m pytest tests/ -q` (DB-gated sa-key tests need the scratch-DB env vars; run them with the recipe in Global Constraints).
- [ ] Rebase check: `git fetch origin` then `git log HEAD..origin/Nggaev-v2` — if base moved, rebase onto `origin/Nggaev-v2`, resolve, re-run suite.
- [ ] `superpowers:finishing-a-development-branch` (push to the working branch — user decides).
- [ ] Worklog entry in `docs/memory/MASTER_MEMORY.md` + a row in `docs/memory/INDEX.md` (next-free worklog number — check the current max).
- [ ] `git mv` this plan into `docs/superpowers/plans/shipped/`.
- [ ] De-stale reference docs: `docs/fleet/worker-pc-setup.md` (the "rough edge (still manual)" SA-key step is now web-driven), `docs/DATABASE.md` (two new tables), `docs/CODE_MAP.md` (new modules), `.env.example` (note that SA-key distribution is now automatic; `AUTH_TOKEN` must be a real secret for the key vault), `README.md`/`docs/HOW_IT_WORKS.md` if they describe worker credential setup.
