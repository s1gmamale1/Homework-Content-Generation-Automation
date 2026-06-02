# Edu-Homework Thin-Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a FastAPI microservice that accepts curriculum PDF uploads, extracts a Table of Contents via Gemini, runs the subject-specific homework prompt pipeline on a chosen section, and returns an assembled Markdown file. Includes a throwaway test UI that consumes the same `/api/v1/...` endpoints.

**Architecture:** Layered FastAPI app — `models/` (SQLAlchemy ORM), `schemas/` (Pydantic), `repositories/` (data access), `services/` (Gemini client, prompt loader, events bus, TOC extractor, pipeline orchestrator), `api/v1/` (REST + SSE routes). State persisted in Postgres via SQLAlchemy 2.0 async + Alembic. Long-running work runs in `asyncio.create_task` with an in-process pub/sub bus that SSE handlers replay from DB on reconnect. Throwaway frontend is plain static HTML+JS in `frontend/`, mounted at `/ui`, calling the public API.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0 (async, asyncpg), Alembic, pydantic-settings, sse-starlette, google-genai SDK, PostgreSQL (Docker for dev), Tailwind CDN.

**Reference spec:** `docs/superpowers/specs/2026-04-28-edu-homework-thin-slice-design.md`

**Conventions enforced throughout:**
- No tests (per project feedback — fast-iteration mode).
- No `Co-Authored-By` in commit messages.
- Run `uv sync` after every `pyproject.toml` change before committing.
- Imports use specific names: `from sqlalchemy.orm import DeclarativeBase`, not `from sqlalchemy import orm`.
- Frontend JS uses safe DOM methods (`createElement`, `textContent`) — never `innerHTML` with interpolated server values.
- Verification is manual smoke: start the app, hit endpoints, observe logs.

---

## File structure (final)

```
edu-homework/
├── .env.example
├── .gitignore
├── alembic.ini
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py
├── docker-compose.yml
├── main.py
├── pyproject.toml
├── uv.lock
├── app/
│   ├── __init__.py
│   ├── auth.py
│   ├── config.py
│   ├── db.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── book.py
│   │   ├── toc_entry.py
│   │   ├── homework_job.py
│   │   └── phase_output.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── book.py
│   │   ├── toc.py
│   │   ├── job.py
│   │   └── events.py
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── books.py
│   │   ├── toc_entries.py
│   │   ├── jobs.py
│   │   └── phase_outputs.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── prompts.py
│   │   ├── flows.py
│   │   ├── events_bus.py
│   │   ├── gemini.py
│   │   ├── toc_extractor.py
│   │   └── pipeline.py
│   └── api/
│       ├── __init__.py
│       └── v1/
│           ├── __init__.py
│           ├── health.py
│           ├── books.py
│           └── jobs.py
├── frontend/
│   ├── index.html
│   ├── book.html
│   ├── job.html
│   ├── app.css
│   └── app.js
└── prompts/                      (untouched)
```

---

## Task 1: Project bootstrap

**Files:**
- Create: `.gitignore`, `.env.example`, `docker-compose.yml`, `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/.gitkeep`
- Modify: `pyproject.toml`
- Create empty: `app/__init__.py`, `app/models/__init__.py`, `app/schemas/__init__.py`, `app/repositories/__init__.py`, `app/services/__init__.py`, `app/api/__init__.py`, `app/api/v1/__init__.py`

- [ ] **Step 1: Initialize git repo**

```bash
cd /Users/jakhongir/PycharmProjects/Edu-Homework
git init
git add main.py pyproject.toml prompts/ test_main.http uv.lock docs/
git commit -m "chore: snapshot existing project state"
```

- [ ] **Step 2: Add `.gitignore`**

Create `.gitignore`:
```
__pycache__/
*.pyc
.venv/
.env
.idea/
.DS_Store
*.egg-info/
.pytest_cache/
dist/
build/
```

- [ ] **Step 3: Update `pyproject.toml` with dependencies**

Replace `pyproject.toml`:
```toml
[project]
name = "edu-homework"
version = "0.1.0"
description = "Curriculum-driven homework generation microservice"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.136.1",
    "uvicorn[standard]>=0.46.0",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30.0",
    "alembic>=1.14.0",
    "pydantic-settings>=2.7.0",
    "python-multipart>=0.0.20",
    "sse-starlette>=2.1.3",
    "google-genai>=0.3.0",
]
```

- [ ] **Step 4: Run `uv sync`**

```bash
uv sync
```
Expected: `Resolved N packages` then `Installed N packages`.

- [ ] **Step 5: Create `.env.example`**

```
DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5432/edu_homework
GEMINI_API_KEY=replace-me
GEMINI_MODEL=gemini-2.0-flash-exp
MAX_FILE_MB=50
ENABLE_DOCS=true
ALLOW_ORIGINS=*
```

Then copy to `.env` and fill in `GEMINI_API_KEY`:
```bash
cp .env.example .env
```

- [ ] **Step 6: Create `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: edu
      POSTGRES_PASSWORD: edu
      POSTGRES_DB: edu_homework
    ports:
      - "5432:5432"
    volumes:
      - edu_pgdata:/var/lib/postgresql/data

volumes:
  edu_pgdata:
```

- [ ] **Step 7: Start Postgres**

```bash
docker compose up -d
```
Expected: `Container edu-homework-postgres-1 Started`.

- [ ] **Step 8: Initialize Alembic**

```bash
uv run alembic init -t async alembic
```
Expected: creates `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/`.

- [ ] **Step 9: Patch `alembic.ini` to read DB URL from env**

In `alembic.ini`, leave `sqlalchemy.url =` empty (we'll set it from `env.py`).

- [ ] **Step 10: Replace `alembic/env.py`**

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.models.base import Base
from app.models import book, toc_entry, homework_job, phase_output  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 11: Create empty `__init__.py` files**

```bash
mkdir -p app/models app/schemas app/repositories app/services app/api/v1
touch app/__init__.py app/models/__init__.py app/schemas/__init__.py \
      app/repositories/__init__.py app/services/__init__.py \
      app/api/__init__.py app/api/v1/__init__.py
```

- [ ] **Step 12: Commit**

```bash
git add .
git commit -m "chore: bootstrap deps, alembic, docker postgres"
```

---

## Task 2: Config + DB infrastructure

**Files:**
- Create: `app/config.py`, `app/db.py`

- [ ] **Step 1: Create `app/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    gemini_api_key: str
    gemini_model: str = "gemini-2.0-flash-exp"
    max_file_mb: int = 50
    enable_docs: bool = False
    allow_origins: str = "*"


settings = Settings()
```

- [ ] **Step 2: Create `app/db.py`**

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
```

- [ ] **Step 3: Smoke verify config loads**

```bash
uv run python -c "from app.config import settings; print(settings.database_url)"
```
Expected: prints `postgresql+asyncpg://edu:edu@localhost:5432/edu_homework`.

- [ ] **Step 4: Commit**

```bash
git add app/config.py app/db.py
git commit -m "feat: settings via pydantic-settings + async SQLAlchemy session factory"
```

---

## Task 3: ORM models

**Files:**
- Create: `app/models/base.py`, `app/models/book.py`, `app/models/toc_entry.py`, `app/models/homework_job.py`, `app/models/phase_output.py`

- [ ] **Step 1: Create `app/models/base.py`**

```python
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UUIDPK:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
```

- [ ] **Step 2: Create `app/models/book.py`**

```python
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPK


class Book(Base, UUIDPK, Timestamps):
    __tablename__ = "books"

    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gemini_file_uri: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    gemini_file_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    toc_entries: Mapped[list["TOCEntry"]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_books_content_sha256", "content_sha256"),)


from app.models.toc_entry import TOCEntry  # noqa: E402
```

- [ ] **Step 3: Create `app/models/toc_entry.py`**

```python
from typing import Optional
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPK


class TOCEntry(Base, UUIDPK, Timestamps):
    __tablename__ = "toc_entries"

    book_id: Mapped[UUID] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    chapter_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    chapter_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    section_number: Mapped[str] = mapped_column(String(32), nullable=False)
    section_title: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    page_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)

    book: Mapped["Book"] = relationship(back_populates="toc_entries")

    __table_args__ = (Index("ix_toc_entries_book_id_order", "book_id", "order_index"),)


from app.models.book import Book  # noqa: E402
```

- [ ] **Step 4: Create `app/models/homework_job.py`**

```python
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPK


class HomeworkJob(Base, UUIDPK, Timestamps):
    __tablename__ = "homework_jobs"

    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id"), nullable=False)
    toc_entry_id: Mapped[UUID] = mapped_column(ForeignKey("toc_entries.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    difficulty: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_phase: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assembled_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    phase_outputs: Mapped[list["PhaseOutput"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="PhaseOutput.phase_order"
    )

    __table_args__ = (
        Index("ix_homework_jobs_book_toc", "book_id", "toc_entry_id"),
        Index("ix_homework_jobs_status", "status"),
    )


from app.models.phase_output import PhaseOutput  # noqa: E402
```

- [ ] **Step 5: Create `app/models/phase_output.py`**

```python
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPK


class PhaseOutput(Base, UUIDPK):
    __tablename__ = "phase_outputs"

    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("homework_jobs.id", ondelete="CASCADE"), nullable=False
    )
    phase_name: Mapped[str] = mapped_column(String(64), nullable=False)
    phase_order: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    output_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tokens_input: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["HomeworkJob"] = relationship(back_populates="phase_outputs")

    __table_args__ = (UniqueConstraint("job_id", "phase_order", name="uq_phase_output_job_order"),)


from app.models.homework_job import HomeworkJob  # noqa: E402
```

- [ ] **Step 6: Update `app/models/__init__.py`**

```python
from app.models.base import Base
from app.models.book import Book
from app.models.toc_entry import TOCEntry
from app.models.homework_job import HomeworkJob
from app.models.phase_output import PhaseOutput

__all__ = ["Base", "Book", "TOCEntry", "HomeworkJob", "PhaseOutput"]
```

- [ ] **Step 7: Smoke verify models import without errors**

```bash
uv run python -c "from app.models import Base, Book, TOCEntry, HomeworkJob, PhaseOutput; print('ok')"
```
Expected: `ok`.

- [ ] **Step 8: Commit**

```bash
git add app/models/
git commit -m "feat: ORM models for books, toc_entries, homework_jobs, phase_outputs"
```

---

## Task 4: First Alembic migration

**Files:**
- Create: `alembic/versions/0001_initial.py` (autogenerated)

- [ ] **Step 1: Generate migration**

```bash
uv run alembic revision --autogenerate -m "initial"
```
Expected: `Generating alembic/versions/<hash>_initial.py`.

- [ ] **Step 2: Rename the generated file**

Rename to `alembic/versions/0001_initial.py` for stable ordering. Open the file, ensure it created the four tables (`books`, `toc_entries`, `homework_jobs`, `phase_outputs`) plus indexes and the unique constraint.

- [ ] **Step 3: Apply the migration**

```bash
uv run alembic upgrade head
```
Expected: `Running upgrade  -> 0001, initial`.

- [ ] **Step 4: Smoke verify tables exist**

```bash
docker compose exec postgres psql -U edu -d edu_homework -c "\dt"
```
Expected: lists `books`, `toc_entries`, `homework_jobs`, `phase_outputs`, `alembic_version`.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/
git commit -m "feat: initial alembic migration creating all tables"
```

---

## Task 5: Pydantic API schemas

**Files:**
- Create: `app/schemas/book.py`, `app/schemas/toc.py`, `app/schemas/job.py`, `app/schemas/events.py`, `app/schemas/__init__.py`

- [ ] **Step 1: Create `app/schemas/toc.py`**

```python
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TOCEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chapter_number: Optional[str] = None
    chapter_title: Optional[str] = None
    section_number: str
    section_title: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    order_index: int


class TOCEntryExtracted(BaseModel):
    chapter_number: Optional[str] = None
    chapter_title: Optional[str] = None
    section_number: str
    section_title: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None


class ExtractedTOC(BaseModel):
    entries: list[TOCEntryExtracted]
```

- [ ] **Step 2: Create `app/schemas/book.py`**

```python
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.toc import TOCEntryOut


class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject: str
    original_filename: str
    status: str
    error_message: Optional[str] = None
    gemini_file_expires_at: Optional[datetime] = None
    toc: Optional[list[TOCEntryOut]] = None
```

- [ ] **Step 3: Create `app/schemas/job.py`**

```python
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PhaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    phase_name: str
    phase_order: int
    status: str
    output_md: Optional[str] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    book_id: UUID
    toc_entry_id: UUID
    subject: str
    difficulty: Optional[str] = None
    status: str
    current_phase: Optional[str] = None
    error_message: Optional[str] = None
    assembled_md: Optional[str] = None
    phases: list[PhaseOut] = []


class GenerateRequest(BaseModel):
    force: bool = False
```

- [ ] **Step 4: Create `app/schemas/events.py`**

```python
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.toc import TOCEntryOut


class TOCStatusEvent(BaseModel):
    status: Literal["uploading", "toc_extracting"]


class TOCReadyEvent(BaseModel):
    entries: list[TOCEntryOut]


class TOCErrorEvent(BaseModel):
    message: str


class PhaseStartedEvent(BaseModel):
    phase_name: str
    phase_order: int


class PhaseCompletedEvent(BaseModel):
    phase_name: str
    phase_order: int
    output_md: str
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None


class DifficultyClassifiedEvent(BaseModel):
    difficulty: Literal["easy", "hard"]


class JobCompletedEvent(BaseModel):
    job_id: str
    download_url: str


class JobErrorEvent(BaseModel):
    phase_name: Optional[str] = None
    message: str
```

- [ ] **Step 5: Update `app/schemas/__init__.py`**

```python
from app.schemas.book import BookOut
from app.schemas.toc import TOCEntryOut, TOCEntryExtracted, ExtractedTOC
from app.schemas.job import JobOut, PhaseOut, GenerateRequest

__all__ = [
    "BookOut",
    "TOCEntryOut",
    "TOCEntryExtracted",
    "ExtractedTOC",
    "JobOut",
    "PhaseOut",
    "GenerateRequest",
]
```

- [ ] **Step 6: Smoke verify**

```bash
uv run python -c "from app.schemas import BookOut, JobOut, ExtractedTOC; print('ok')"
```
Expected: `ok`.

- [ ] **Step 7: Commit**

```bash
git add app/schemas/
git commit -m "feat: pydantic api schemas + sse event payloads"
```

---

## Task 6: Repositories

**Files:**
- Create: `app/repositories/books.py`, `app/repositories/toc_entries.py`, `app/repositories/jobs.py`, `app/repositories/phase_outputs.py`, `app/repositories/__init__.py`

- [ ] **Step 1: Create `app/repositories/books.py`**

```python
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Book


async def create(
    session: AsyncSession,
    *,
    subject: str,
    original_filename: str,
    content_sha256: str,
    file_size_bytes: int,
    status: str = "uploading",
) -> Book:
    book = Book(
        subject=subject,
        original_filename=original_filename,
        content_sha256=content_sha256,
        file_size_bytes=file_size_bytes,
        status=status,
    )
    session.add(book)
    await session.flush()
    return book


async def get(session: AsyncSession, book_id: UUID) -> Optional[Book]:
    return await session.get(Book, book_id)


async def get_with_toc(session: AsyncSession, book_id: UUID) -> Optional[Book]:
    stmt = select(Book).where(Book.id == book_id).options(selectinload(Book.toc_entries))
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_ready_by_hash(
    session: AsyncSession, content_sha256: str, subject: str
) -> Optional[Book]:
    stmt = (
        select(Book)
        .where(
            Book.content_sha256 == content_sha256,
            Book.subject == subject,
            Book.status == "toc_ready",
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_gemini_file(
    session: AsyncSession, book_id: UUID, *, file_uri: str, expires_at: datetime
) -> None:
    book = await session.get(Book, book_id)
    if book is None:
        return
    book.gemini_file_uri = file_uri
    book.gemini_file_expires_at = expires_at


async def set_status(
    session: AsyncSession, book_id: UUID, status: str, error_message: Optional[str] = None
) -> None:
    book = await session.get(Book, book_id)
    if book is None:
        return
    book.status = status
    if error_message is not None:
        book.error_message = error_message


async def list_running_for_sweep(session: AsyncSession) -> list[Book]:
    stmt = select(Book).where(Book.status.in_(["uploading", "toc_extracting"]))
    return list((await session.execute(stmt)).scalars().all())
```

- [ ] **Step 2: Create `app/repositories/toc_entries.py`**

```python
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TOCEntry
from app.schemas import TOCEntryExtracted


async def bulk_create(
    session: AsyncSession, book_id: UUID, entries: list[TOCEntryExtracted]
) -> list[TOCEntry]:
    rows: list[TOCEntry] = []
    for idx, e in enumerate(entries):
        row = TOCEntry(
            book_id=book_id,
            chapter_number=e.chapter_number,
            chapter_title=e.chapter_title,
            section_number=e.section_number,
            section_title=e.section_title,
            page_start=e.page_start,
            page_end=e.page_end,
            order_index=idx,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def list_for_book(session: AsyncSession, book_id: UUID) -> list[TOCEntry]:
    stmt = (
        select(TOCEntry)
        .where(TOCEntry.book_id == book_id)
        .order_by(TOCEntry.order_index)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get(session: AsyncSession, toc_entry_id: UUID) -> TOCEntry | None:
    return await session.get(TOCEntry, toc_entry_id)
```

- [ ] **Step 3: Create `app/repositories/jobs.py`**

```python
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import HomeworkJob


async def create(
    session: AsyncSession,
    *,
    book_id: UUID,
    toc_entry_id: UUID,
    subject: str,
    status: str = "pending",
) -> HomeworkJob:
    job = HomeworkJob(
        book_id=book_id,
        toc_entry_id=toc_entry_id,
        subject=subject,
        status=status,
    )
    session.add(job)
    await session.flush()
    return job


async def get(session: AsyncSession, job_id: UUID) -> Optional[HomeworkJob]:
    return await session.get(HomeworkJob, job_id)


async def get_with_phases(session: AsyncSession, job_id: UUID) -> Optional[HomeworkJob]:
    stmt = (
        select(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .options(selectinload(HomeworkJob.phase_outputs))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_active_for_section(
    session: AsyncSession, book_id: UUID, toc_entry_id: UUID
) -> Optional[HomeworkJob]:
    stmt = (
        select(HomeworkJob)
        .where(
            HomeworkJob.book_id == book_id,
            HomeworkJob.toc_entry_id == toc_entry_id,
            HomeworkJob.status.in_(["pending", "running", "done"]),
        )
        .order_by(HomeworkJob.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_status(
    session: AsyncSession,
    job_id: UUID,
    status: str,
    *,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
    error_message: Optional[str] = None,
    current_phase: Optional[str] = None,
    assembled_md: Optional[str] = None,
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.status = status
    if started_at is not None:
        job.started_at = started_at
    if completed_at is not None:
        job.completed_at = completed_at
    if error_message is not None:
        job.error_message = error_message
    if current_phase is not None:
        job.current_phase = current_phase
    if assembled_md is not None:
        job.assembled_md = assembled_md


async def set_difficulty(session: AsyncSession, job_id: UUID, difficulty: str) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.difficulty = difficulty


async def list_running_for_sweep(session: AsyncSession) -> list[HomeworkJob]:
    stmt = select(HomeworkJob).where(HomeworkJob.status.in_(["pending", "running"]))
    return list((await session.execute(stmt)).scalars().all())
```

- [ ] **Step 4: Create `app/repositories/phase_outputs.py`**

```python
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PhaseOutput


async def create(
    session: AsyncSession,
    *,
    job_id: UUID,
    phase_name: str,
    phase_order: int,
    prompt_hash: str,
    model_name: str,
    status: str = "pending",
) -> PhaseOutput:
    po = PhaseOutput(
        job_id=job_id,
        phase_name=phase_name,
        phase_order=phase_order,
        prompt_hash=prompt_hash,
        model_name=model_name,
        status=status,
    )
    session.add(po)
    await session.flush()
    return po


async def list_for_job(session: AsyncSession, job_id: UUID) -> list[PhaseOutput]:
    stmt = (
        select(PhaseOutput)
        .where(PhaseOutput.job_id == job_id)
        .order_by(PhaseOutput.phase_order)
    )
    return list((await session.execute(stmt)).scalars().all())


async def set_status(
    session: AsyncSession,
    phase_output_id: UUID,
    status: str,
    *,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
    output_md: Optional[str] = None,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    po = await session.get(PhaseOutput, phase_output_id)
    if po is None:
        return
    po.status = status
    if started_at is not None:
        po.started_at = started_at
    if completed_at is not None:
        po.completed_at = completed_at
    if output_md is not None:
        po.output_md = output_md
    if tokens_input is not None:
        po.tokens_input = tokens_input
    if tokens_output is not None:
        po.tokens_output = tokens_output
    if error_message is not None:
        po.error_message = error_message


async def list_running_for_sweep(session: AsyncSession) -> list[PhaseOutput]:
    stmt = select(PhaseOutput).where(PhaseOutput.status.in_(["pending", "running"]))
    return list((await session.execute(stmt)).scalars().all())
```

- [ ] **Step 5: Update `app/repositories/__init__.py`**

```python
from app.repositories import books, toc_entries, jobs, phase_outputs

__all__ = ["books", "toc_entries", "jobs", "phase_outputs"]
```

- [ ] **Step 6: Smoke verify**

```bash
uv run python -c "from app.repositories import books, toc_entries, jobs, phase_outputs; print('ok')"
```
Expected: `ok`.

- [ ] **Step 7: Commit**

```bash
git add app/repositories/
git commit -m "feat: repositories for books, toc_entries, jobs, phase_outputs"
```

---

## Task 7: Auth stub + health route + minimal app boot

**Files:**
- Create: `app/auth.py`, `app/api/v1/health.py`, `main.py` (replace existing)

- [ ] **Step 1: Create `app/auth.py`**

```python
from typing import Optional

from fastapi import Header


async def get_current_user(x_user_id: Optional[str] = Header(default=None)) -> dict:
    return {"user_id": x_user_id or "test-user"}
```

- [ ] **Step 2: Create `app/api/v1/health.py`**

```python
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter()


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    db_ok = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as e:
        db_ok = f"error: {e}"
    return {"status": "ok", "db": db_ok}
```

- [ ] **Step 3: Create `app/api/v1/__init__.py`**

```python
from fastapi import APIRouter

from app.api.v1 import health

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health.router, tags=["meta"])
```

- [ ] **Step 4: Replace `main.py`**

```python
from fastapi import FastAPI

from app.api.v1 import api_v1_router
from app.config import settings

app = FastAPI(
    title="Edu-Homework",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
)

app.include_router(api_v1_router)


@app.get("/health")
async def root_health() -> dict:
    return {"status": "ok"}
```

- [ ] **Step 5: Smoke verify app boots and health works**

```bash
uv run uvicorn main:app --port 8000 &
sleep 2
curl -s http://localhost:8000/api/v1/health
kill %1
```
Expected: `{"status":"ok","db":"ok"}`.

- [ ] **Step 6: Commit**

```bash
git add app/auth.py app/api/ main.py
git commit -m "feat: app skeleton, auth stub, /api/v1/health endpoint"
```

---

## Task 8: Prompt loader + per-subject flows

**Files:**
- Create: `app/services/prompts.py`, `app/services/flows.py`

- [ ] **Step 1: Create `app/services/flows.py`**

```python
SUBJECT_FLOWS: dict[str, dict] = {
    "biology": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "english": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "reading", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "geometriya-g7-11": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "history": {
        "has_classify": False,
        "easy": [],
        "hard": ["preview", "flashcards", "memory-sprint", "game-breaks",
                 "consolidation", "final-challenge", "reflection"],
    },
    "kimyo-g7-11": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "math-algebra": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
    },
    "physics": {
        "has_classify": True,
        "easy": ["preview-easy", "flashcards", "memory-sprint", "game-breaks", "reflection"],
        "hard": ["preview-hard", "flashcards", "memory-sprint", "game-breaks",
                 "real-life", "consolidation", "final-challenge", "reflection"],
    },
}


SUPPORTED_SUBJECTS: list[str] = sorted(SUBJECT_FLOWS.keys())
```

> **Note for the engineer:** Before committing, open each `prompts/<subject>/flow.md` and verify the `easy` / `hard` lists above match the canonical sequences. Adjust if a subject's `flow.md` differs (especially `english` which has `reading` that no other subject has). Sequences exclude `extract` and `classify` — those are pipeline-internal and not assembled into the `.md`.

- [ ] **Step 2: Create `app/services/prompts.py`**

```python
import hashlib
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

_cache: dict[str, dict[str, str]] = {}
_hash_cache: dict[str, dict[str, str]] = {}


def _load_subject(subject: str) -> tuple[dict[str, str], dict[str, str]]:
    subject_dir = PROMPTS_DIR / subject
    if not subject_dir.is_dir():
        raise FileNotFoundError(f"Prompt directory not found: {subject_dir}")
    bodies: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for md in subject_dir.glob("*.md"):
        body = md.read_text(encoding="utf-8")
        bodies[md.stem] = body
        hashes[md.stem] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return bodies, hashes


def load_all() -> None:
    from app.services.flows import SUPPORTED_SUBJECTS

    for subject in SUPPORTED_SUBJECTS:
        bodies, hashes = _load_subject(subject)
        _cache[subject] = bodies
        _hash_cache[subject] = hashes


def get_prompt(subject: str, phase_name: str) -> str:
    if subject not in _cache:
        bodies, hashes = _load_subject(subject)
        _cache[subject] = bodies
        _hash_cache[subject] = hashes
    if phase_name not in _cache[subject]:
        raise KeyError(f"Prompt {subject}/{phase_name}.md not found")
    return _cache[subject][phase_name]


def get_prompt_hash(subject: str, phase_name: str) -> str:
    if subject not in _hash_cache:
        get_prompt(subject, phase_name)
    return _hash_cache[subject][phase_name]
```

- [ ] **Step 3: Smoke verify**

```bash
uv run python -c "
from app.services.prompts import load_all, get_prompt, get_prompt_hash
load_all()
print(get_prompt_hash('biology', 'classify')[:12])
print(len(get_prompt('biology', 'flashcards')))
"
```
Expected: 12-char hex prefix and a non-zero length.

- [ ] **Step 4: Commit**

```bash
git add app/services/prompts.py app/services/flows.py
git commit -m "feat: prompt loader and per-subject phase flows"
```

---

## Task 9: Events bus (in-process pub/sub)

**Files:**
- Create: `app/services/events_bus.py`

- [ ] **Step 1: Create `app/services/events_bus.py`**

```python
import asyncio
from collections import defaultdict
from typing import Any

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)


def subscribe(resource_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers[resource_id].add(q)
    return q


def unsubscribe(resource_id: str, q: asyncio.Queue) -> None:
    _subscribers[resource_id].discard(q)
    if not _subscribers[resource_id]:
        _subscribers.pop(resource_id, None)


async def publish(resource_id: str, event: str, data: dict[str, Any]) -> None:
    payload = {"event": event, "data": data}
    for q in list(_subscribers.get(resource_id, [])):
        await q.put(payload)


async def close(resource_id: str) -> None:
    for q in list(_subscribers.get(resource_id, [])):
        await q.put(None)
```

- [ ] **Step 2: Smoke verify**

```bash
uv run python -c "
import asyncio
from app.services import events_bus

async def main():
    q = events_bus.subscribe('r1')
    await events_bus.publish('r1', 'hello', {'x': 1})
    print(await q.get())

asyncio.run(main())
"
```
Expected: `{'event': 'hello', 'data': {'x': 1}}`.

- [ ] **Step 3: Commit**

```bash
git add app/services/events_bus.py
git commit -m "feat: in-process pub/sub events bus for sse fanout"
```

---

## Task 10: Gemini client wrapper

**Files:**
- Create: `app/services/gemini.py`

- [ ] **Step 1: Create `app/services/gemini.py`**

```python
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types

from app.config import settings
from app.schemas import ExtractedTOC

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


async def upload_file(path: Path, mime_type: str = "application/pdf") -> tuple[str, datetime]:
    client = _get_client()
    file = await asyncio.to_thread(
        client.files.upload, file=str(path), config={"mime_type": mime_type}
    )
    expires_at = datetime.now(timezone.utc) + timedelta(hours=47)
    return file.name, expires_at


async def extract_toc(file_uri: str, subject: str) -> ExtractedTOC:
    client = _get_client()
    prompt = (
        f"You are reading a {subject} curriculum textbook. "
        "Extract the full Table of Contents as structured JSON. "
        "For every numbered section (e.g., §1, §2 ... or '1.1', '1.2' ...), "
        "produce one entry with: chapter_number (text), chapter_title, "
        "section_number, section_title, page_start, page_end. "
        "If the book is organized by chapters, use the chapter title as 'chapter_title' "
        "for every section under it. Do not invent sections. Order entries as they appear."
    )

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=settings.gemini_model,
        contents=[
            types.Part.from_uri(file_uri=_uri(file_uri), mime_type="application/pdf"),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedTOC,
        ),
    )

    parsed: ExtractedTOC = response.parsed  # type: ignore[assignment]
    return parsed


_EXTRACT_PHASE_PROMPT = (
    'You are reading the attached textbook. The lesson is "{title}" '
    "(section {number}, pages {ps}-{pe}).\n\n"
    "Extract all factual lesson content the textbook teaches on these pages. "
    "Include: key terms with definitions, named processes/mechanisms with steps, "
    "diagrams/visuals (describe them), worked examples, formulas, "
    "organisms/structures with functions, historical references, experiments, "
    "and comparison tables.\n\n"
    "Output as structured Markdown. Be faithful to the source — do not invent."
)


async def extract_lesson_context(
    file_uri: str,
    section_title: str,
    section_number: str,
    page_start: Optional[int],
    page_end: Optional[int],
) -> tuple[str, Optional[int], Optional[int]]:
    client = _get_client()
    prompt = _EXTRACT_PHASE_PROMPT.format(
        title=section_title,
        number=section_number,
        ps=page_start if page_start is not None else "?",
        pe=page_end if page_end is not None else "?",
    )
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=settings.gemini_model,
        contents=[
            types.Part.from_uri(file_uri=_uri(file_uri), mime_type="application/pdf"),
            prompt,
        ],
    )
    return response.text or "", _tokens_in(response), _tokens_out(response)


async def run_phase_prompt(
    *,
    phase_prompt: str,
    file_uri: str,
    lesson_context: str,
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
) -> tuple[str, Optional[int], Optional[int]]:
    client = _get_client()

    user_blocks: list[str] = ["## Lesson context", lesson_context]
    if difficulty is not None:
        user_blocks.extend(["", "## Difficulty", difficulty.upper()])
    if prior_outputs:
        user_blocks.append("\n## Prior phase outputs")
        for name, body in prior_outputs.items():
            user_blocks.append(f"\n### {name}\n{body}")
    user_text = "\n".join(user_blocks)

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=settings.gemini_model,
        contents=[
            types.Part.from_uri(file_uri=_uri(file_uri), mime_type="application/pdf"),
            user_text,
        ],
        config=types.GenerateContentConfig(system_instruction=phase_prompt),
    )
    return response.text or "", _tokens_in(response), _tokens_out(response)


def _uri(file_name: str) -> str:
    if file_name.startswith("https://") or file_name.startswith("files/"):
        return file_name
    return f"files/{file_name}"


def _tokens_in(response) -> Optional[int]:
    try:
        return response.usage_metadata.prompt_token_count
    except AttributeError:
        return None


def _tokens_out(response) -> Optional[int]:
    try:
        return response.usage_metadata.candidates_token_count
    except AttributeError:
        return None
```

- [ ] **Step 2: Smoke verify import**

```bash
uv run python -c "from app.services import gemini; print('ok')"
```
Expected: `ok`.

> A real Gemini call is exercised end-to-end in Task 18.

- [ ] **Step 3: Commit**

```bash
git add app/services/gemini.py
git commit -m "feat: gemini client wrapper - upload, toc extract, lesson extract, run phase"
```

---

## Task 11: TOC extractor service

**Files:**
- Create: `app/services/toc_extractor.py`

- [ ] **Step 1: Create `app/services/toc_extractor.py`**

```python
import logging
from pathlib import Path
from uuid import UUID

from app.db import SessionLocal
from app.repositories import books as books_repo
from app.repositories import toc_entries as toc_repo
from app.schemas import TOCEntryOut
from app.services import events_bus, gemini

log = logging.getLogger(__name__)


async def run(book_id: UUID, file_path: Path, subject: str) -> None:
    resource_id = f"book:{book_id}"
    try:
        await events_bus.publish(resource_id, "status", {"status": "uploading"})

        file_uri, expires_at = await gemini.upload_file(file_path)

        async with SessionLocal() as session:
            await books_repo.set_gemini_file(
                session, book_id, file_uri=file_uri, expires_at=expires_at
            )
            await books_repo.set_status(session, book_id, "toc_extracting")
            await session.commit()

        await events_bus.publish(resource_id, "status", {"status": "toc_extracting"})

        extracted = await gemini.extract_toc(file_uri, subject)

        async with SessionLocal() as session:
            rows = await toc_repo.bulk_create(session, book_id, extracted.entries)
            await books_repo.set_status(session, book_id, "toc_ready")
            await session.commit()
            entries_out = [TOCEntryOut.model_validate(r) for r in rows]

        await events_bus.publish(
            resource_id,
            "toc_ready",
            {"entries": [e.model_dump(mode="json") for e in entries_out]},
        )
    except Exception as exc:
        log.exception("TOC extraction failed for book %s", book_id)
        async with SessionLocal() as session:
            await books_repo.set_status(session, book_id, "failed", error_message=str(exc))
            await session.commit()
        await events_bus.publish(resource_id, "error", {"message": str(exc)})
    finally:
        await events_bus.close(resource_id)
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass
```

- [ ] **Step 2: Commit**

```bash
git add app/services/toc_extractor.py
git commit -m "feat: toc extraction background task service"
```

---

## Task 12: Pipeline orchestrator

**Files:**
- Create: `app/services/pipeline.py`

- [ ] **Step 1: Create `app/services/pipeline.py`**

```python
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.config import settings
from app.db import SessionLocal
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import phase_outputs as phase_repo
from app.repositories import toc_entries as toc_repo
from app.services import events_bus, gemini
from app.services.flows import SUBJECT_FLOWS
from app.services.prompts import get_prompt, get_prompt_hash

log = logging.getLogger(__name__)

_INTERNAL_PHASES = {"extract", "classify"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run(job_id: UUID) -> None:
    resource_id = f"job:{job_id}"
    try:
        async with SessionLocal() as session:
            job = await jobs_repo.get(session, job_id)
            if job is None:
                return
            book = await books_repo.get(session, job.book_id)
            section = await toc_repo.get(session, job.toc_entry_id)
            if book is None or section is None or book.gemini_file_uri is None:
                raise RuntimeError("Job is missing book or section context")
            subject = book.subject
            file_uri = book.gemini_file_uri
            section_data = {
                "title": section.section_title,
                "number": section.section_number,
                "page_start": section.page_start,
                "page_end": section.page_end,
            }

        flow = SUBJECT_FLOWS[subject]
        sequence: list[str] = ["extract"]
        if flow["has_classify"]:
            sequence.append("classify")
        else:
            sequence.extend(flow["hard"])

        async with SessionLocal() as session:
            await jobs_repo.set_status(session, job_id, "running", started_at=_utcnow())
            await session.commit()

        difficulty: Optional[str] = None
        prior_outputs: dict[str, str] = {}
        lesson_context: Optional[str] = None
        phase_order = 0

        while phase_order < len(sequence):
            phase_name = sequence[phase_order]
            await _emit_started(resource_id, phase_name, phase_order)

            try:
                output_md, tin, tout, _ph = await _execute_phase(
                    job_id=job_id,
                    phase_name=phase_name,
                    phase_order=phase_order,
                    subject=subject,
                    file_uri=file_uri,
                    section=section_data,
                    lesson_context=lesson_context,
                    prior_outputs=prior_outputs,
                    difficulty=difficulty,
                )
            except Exception as exc:
                log.exception("Phase %s failed for job %s", phase_name, job_id)
                async with SessionLocal() as session:
                    await jobs_repo.set_status(
                        session, job_id, "failed",
                        completed_at=_utcnow(),
                        error_message=f"{phase_name}: {exc}",
                    )
                    await session.commit()
                await events_bus.publish(
                    resource_id, "error", {"phase_name": phase_name, "message": str(exc)}
                )
                return

            await events_bus.publish(
                resource_id,
                "phase_completed",
                {
                    "phase_name": phase_name,
                    "phase_order": phase_order,
                    "output_md": output_md,
                    "tokens_input": tin,
                    "tokens_output": tout,
                },
            )

            if phase_name == "extract":
                lesson_context = output_md
            elif phase_name == "classify":
                difficulty = _parse_classify(output_md)
                async with SessionLocal() as session:
                    await jobs_repo.set_difficulty(session, job_id, difficulty)
                    await session.commit()
                await events_bus.publish(
                    resource_id, "difficulty_classified", {"difficulty": difficulty}
                )
                sequence.extend(flow[difficulty])
            else:
                prior_outputs[phase_name] = output_md

            phase_order += 1

        assembled = await _assemble(job_id)
        async with SessionLocal() as session:
            await jobs_repo.set_status(
                session, job_id, "done",
                completed_at=_utcnow(),
                assembled_md=assembled,
            )
            await session.commit()
        await events_bus.publish(
            resource_id,
            "job_completed",
            {"job_id": str(job_id), "download_url": f"/api/v1/jobs/{job_id}/download"},
        )
    except Exception as exc:
        log.exception("Pipeline crashed for job %s", job_id)
        async with SessionLocal() as session:
            await jobs_repo.set_status(
                session, job_id, "failed",
                completed_at=_utcnow(),
                error_message=str(exc),
            )
            await session.commit()
        await events_bus.publish(resource_id, "error", {"message": str(exc)})
    finally:
        await events_bus.close(resource_id)


async def _emit_started(resource_id: str, phase_name: str, phase_order: int) -> None:
    await events_bus.publish(
        resource_id,
        "phase_started",
        {"phase_name": phase_name, "phase_order": phase_order},
    )


async def _execute_phase(
    *,
    job_id: UUID,
    phase_name: str,
    phase_order: int,
    subject: str,
    file_uri: str,
    section: dict,
    lesson_context: Optional[str],
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
) -> tuple[str, Optional[int], Optional[int], str]:
    if phase_name == "extract":
        prompt_hash = "builtin:extract:v1"
    else:
        prompt_hash = get_prompt_hash(subject, phase_name)

    async with SessionLocal() as session:
        po = await phase_repo.create(
            session,
            job_id=job_id,
            phase_name=phase_name,
            phase_order=phase_order,
            prompt_hash=prompt_hash,
            model_name=settings.gemini_model,
        )
        await phase_repo.set_status(
            session, po.id, "running", started_at=_utcnow()
        )
        await jobs_repo.set_status(session, job_id, "running", current_phase=phase_name)
        await session.commit()
        po_id = po.id

    try:
        if phase_name == "extract":
            output_md, tin, tout = await gemini.extract_lesson_context(
                file_uri=file_uri,
                section_title=section["title"],
                section_number=section["number"],
                page_start=section["page_start"],
                page_end=section["page_end"],
            )
        else:
            phase_prompt = get_prompt(subject, phase_name)
            output_md, tin, tout = await gemini.run_phase_prompt(
                phase_prompt=phase_prompt,
                file_uri=file_uri,
                lesson_context=lesson_context or "",
                prior_outputs=prior_outputs,
                difficulty=difficulty,
            )
    except Exception as exc:
        async with SessionLocal() as session:
            await phase_repo.set_status(
                session, po_id, "failed",
                completed_at=_utcnow(),
                error_message=str(exc),
            )
            await session.commit()
        raise

    async with SessionLocal() as session:
        await phase_repo.set_status(
            session, po_id, "done",
            completed_at=_utcnow(),
            output_md=output_md,
            tokens_input=tin,
            tokens_output=tout,
        )
        await session.commit()

    return output_md, tin, tout, prompt_hash


def _parse_classify(output_md: str) -> str:
    upper = output_md.upper()
    if "HARD" in upper:
        return "hard"
    return "easy"


async def _assemble(job_id: UUID) -> str:
    async with SessionLocal() as session:
        phases = await phase_repo.list_for_job(session, job_id)
    parts: list[str] = []
    for p in phases:
        if p.phase_name in _INTERNAL_PHASES:
            continue
        title = p.phase_name.replace("-", " ").title()
        body = p.output_md or "(empty)"
        parts.append(f"## {title}\n\n{body}\n")
    return "\n".join(parts)
```

- [ ] **Step 2: Smoke verify import**

```bash
uv run python -c "from app.services import pipeline; print('ok')"
```
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add app/services/pipeline.py
git commit -m "feat: phase orchestrator - extract, classify, content phases, assembly"
```

---

## Task 13: Books API routes

**Files:**
- Create: `app/api/v1/books.py`
- Modify: `app/api/v1/__init__.py`

- [ ] **Step 1: Create `app/api/v1/books.py`**

```python
import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth import get_current_user
from app.config import settings
from app.db import get_session, SessionLocal
from app.repositories import books as books_repo
from app.schemas import BookOut, TOCEntryOut
from app.services import events_bus, toc_extractor
from app.services.flows import SUPPORTED_SUBJECTS

router = APIRouter(prefix="/books", tags=["books"])


@router.post("", status_code=201)
async def upload_book(
    file: UploadFile = File(...),
    subject: str = Form(...),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> BookOut:
    if subject not in SUPPORTED_SUBJECTS:
        raise HTTPException(400, f"unknown subject; allowed: {SUPPORTED_SUBJECTS}")

    body = await file.read()
    if len(body) > settings.max_file_mb * 1024 * 1024:
        raise HTTPException(413, f"file too large (>{settings.max_file_mb} MB)")
    if len(body) == 0:
        raise HTTPException(400, "empty file")

    sha = hashlib.sha256(body).hexdigest()

    existing = await books_repo.find_ready_by_hash(session, sha, subject)
    if existing is not None:
        return await _book_out_with_toc(session, existing.id)

    book = await books_repo.create(
        session,
        subject=subject,
        original_filename=file.filename or "book.pdf",
        content_sha256=sha,
        file_size_bytes=len(body),
        status="uploading",
    )
    await session.commit()

    tmp = Path(tempfile.gettempdir()) / f"edu-book-{book.id}.pdf"
    tmp.write_bytes(body)

    asyncio.create_task(toc_extractor.run(book.id, tmp, subject))

    return BookOut.model_validate(book)


@router.get("/{book_id}")
async def get_book(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> BookOut:
    return await _book_out_with_toc(session, book_id)


@router.get("/{book_id}/toc/stream")
async def stream_toc(book_id: UUID, request: Request):
    resource_id = f"book:{book_id}"

    async def event_gen():
        async with SessionLocal() as session:
            book = await books_repo.get_with_toc(session, book_id)
            if book is None:
                yield {"event": "error", "data": json.dumps({"message": "book not found"})}
                return

            if book.status in ("uploading", "toc_extracting"):
                yield {"event": "status", "data": json.dumps({"status": book.status})}
            elif book.status == "toc_ready":
                entries = [TOCEntryOut.model_validate(e).model_dump(mode="json")
                           for e in book.toc_entries]
                yield {"event": "toc_ready", "data": json.dumps({"entries": entries})}
                return
            elif book.status == "failed":
                yield {"event": "error",
                       "data": json.dumps({"message": book.error_message or "failed"})}
                return

        q = events_bus.subscribe(resource_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                payload = await q.get()
                if payload is None:
                    break
                yield {"event": payload["event"], "data": json.dumps(payload["data"])}
                if payload["event"] in ("toc_ready", "error"):
                    break
        finally:
            events_bus.unsubscribe(resource_id, q)

    return EventSourceResponse(event_gen())


async def _book_out_with_toc(session: AsyncSession, book_id: UUID) -> BookOut:
    book = await books_repo.get_with_toc(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    out = BookOut.model_validate(book)
    if book.status == "toc_ready":
        out.toc = [TOCEntryOut.model_validate(e) for e in book.toc_entries]
    return out
```

- [ ] **Step 2: Wire books router into v1**

Replace `app/api/v1/__init__.py`:
```python
from fastapi import APIRouter

from app.api.v1 import books, health

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health.router, tags=["meta"])
api_v1_router.include_router(books.router)
```

- [ ] **Step 3: Smoke verify route is registered**

```bash
uv run uvicorn main:app --port 8000 &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/books/00000000-0000-0000-0000-000000000000
kill %1
```
Expected: `404` (route exists, book doesn't).

- [ ] **Step 4: Commit**

```bash
git add app/api/v1/books.py app/api/v1/__init__.py
git commit -m "feat: books api - upload, get, sse toc stream with replay"
```

---

## Task 14: Jobs API routes

**Files:**
- Create: `app/api/v1/jobs.py`
- Modify: `app/api/v1/__init__.py`

- [ ] **Step 1: Create `app/api/v1/jobs.py`**

```python
import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth import get_current_user
from app.db import SessionLocal, get_session
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import toc_entries as toc_repo
from app.schemas import GenerateRequest, JobOut, PhaseOut
from app.services import events_bus, pipeline

router = APIRouter(tags=["jobs"])


@router.post("/books/{book_id}/sections/{toc_entry_id}/generate", status_code=201)
async def generate(
    book_id: UUID,
    toc_entry_id: UUID,
    response: Response,
    body: GenerateRequest = GenerateRequest(),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> JobOut:
    book = await books_repo.get(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    if book.status != "toc_ready":
        raise HTTPException(409, f"book not ready (status={book.status})")
    section = await toc_repo.get(session, toc_entry_id)
    if section is None or section.book_id != book_id:
        raise HTTPException(404, "section not found")

    if not body.force:
        existing = await jobs_repo.find_active_for_section(session, book_id, toc_entry_id)
        if existing is not None:
            response.status_code = 200
            return await _job_out(session, existing.id)

    job = await jobs_repo.create(
        session,
        book_id=book_id,
        toc_entry_id=toc_entry_id,
        subject=book.subject,
        status="pending",
    )
    await session.commit()

    asyncio.create_task(pipeline.run(job.id))
    return await _job_out(session, job.id)


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> JobOut:
    return await _job_out(session, job_id)


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: UUID, request: Request):
    resource_id = f"job:{job_id}"

    async def event_gen():
        async with SessionLocal() as session:
            job = await jobs_repo.get_with_phases(session, job_id)
            if job is None:
                yield {"event": "error", "data": json.dumps({"message": "job not found"})}
                return

            for p in job.phase_outputs:
                if p.status == "done":
                    yield {
                        "event": "phase_completed",
                        "data": json.dumps({
                            "phase_name": p.phase_name,
                            "phase_order": p.phase_order,
                            "output_md": p.output_md or "",
                            "tokens_input": p.tokens_input,
                            "tokens_output": p.tokens_output,
                        }),
                    }
                elif p.status == "running":
                    yield {
                        "event": "phase_started",
                        "data": json.dumps({
                            "phase_name": p.phase_name,
                            "phase_order": p.phase_order,
                        }),
                    }

            if job.difficulty is not None:
                yield {
                    "event": "difficulty_classified",
                    "data": json.dumps({"difficulty": job.difficulty}),
                }

            if job.status == "done":
                yield {
                    "event": "job_completed",
                    "data": json.dumps({
                        "job_id": str(job_id),
                        "download_url": f"/api/v1/jobs/{job_id}/download",
                    }),
                }
                return
            if job.status == "failed":
                yield {
                    "event": "error",
                    "data": json.dumps({"message": job.error_message or "failed"}),
                }
                return

        q = events_bus.subscribe(resource_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                payload = await q.get()
                if payload is None:
                    break
                yield {"event": payload["event"], "data": json.dumps(payload["data"])}
                if payload["event"] in ("job_completed", "error"):
                    break
        finally:
            events_bus.unsubscribe(resource_id, q)

    return EventSourceResponse(event_gen())


@router.get("/jobs/{job_id}/download")
async def download(job_id: UUID, session: AsyncSession = Depends(get_session)):
    job = await jobs_repo.get(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "done" or job.assembled_md is None:
        raise HTTPException(404, "homework not ready")

    filename = f"homework-{job_id}.md"
    return PlainTextResponse(
        job.assembled_md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _job_out(session: AsyncSession, job_id: UUID) -> JobOut:
    job = await jobs_repo.get_with_phases(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    out = JobOut.model_validate(job)
    out.phases = [PhaseOut.model_validate(p) for p in job.phase_outputs]
    return out
```

- [ ] **Step 2: Wire jobs router into v1**

Replace `app/api/v1/__init__.py`:
```python
from fastapi import APIRouter

from app.api.v1 import books, health, jobs

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health.router, tags=["meta"])
api_v1_router.include_router(books.router)
api_v1_router.include_router(jobs.router)
```

- [ ] **Step 3: Smoke verify route registration**

```bash
uv run uvicorn main:app --port 8000 &
sleep 2
curl -s http://localhost:8000/openapi.json | python -c "import sys, json; print('\n'.join(sorted(json.load(sys.stdin)['paths'].keys())))"
kill %1
```
Expected output includes:
```
/api/v1/books
/api/v1/books/{book_id}
/api/v1/books/{book_id}/sections/{toc_entry_id}/generate
/api/v1/books/{book_id}/toc/stream
/api/v1/health
/api/v1/jobs/{job_id}
/api/v1/jobs/{job_id}/download
/api/v1/jobs/{job_id}/stream
/health
```

- [ ] **Step 4: Commit**

```bash
git add app/api/v1/jobs.py app/api/v1/__init__.py
git commit -m "feat: jobs api - generate, get, sse stream with replay, markdown download"
```

---

## Task 15: main.py final wiring (lifespan, orphan sweep, /ui mount, CORS)

**Files:**
- Modify: `main.py`
- Create: `frontend/.gitkeep`

- [ ] **Step 1: Create `frontend/.gitkeep`**

```bash
mkdir -p frontend
touch frontend/.gitkeep
```

- [ ] **Step 2: Replace `main.py`**

```python
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_v1_router
from app.config import settings
from app.db import SessionLocal
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import phase_outputs as phase_repo
from app.services.prompts import load_all as load_prompts

log = logging.getLogger("edu-homework")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_prompts()
    log.info("Prompts loaded")

    async with SessionLocal() as session:
        for b in await books_repo.list_running_for_sweep(session):
            await books_repo.set_status(
                session, b.id, "failed",
                error_message="orphaned: worker restarted",
            )
        for j in await jobs_repo.list_running_for_sweep(session):
            await jobs_repo.set_status(
                session, j.id, "failed",
                completed_at=datetime.now(timezone.utc),
                error_message="orphaned: worker restarted",
            )
        for p in await phase_repo.list_running_for_sweep(session):
            await phase_repo.set_status(
                session, p.id, "failed",
                completed_at=datetime.now(timezone.utc),
                error_message="orphaned: worker restarted",
            )
        await session.commit()
    log.info("Orphan sweep complete")
    yield


app = FastAPI(
    title="Edu-Homework",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allow_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router)


@app.get("/health")
async def root_health() -> dict:
    return {"status": "ok"}


frontend_dir = Path(__file__).resolve().parent / "frontend"
if frontend_dir.is_dir():
    app.mount("/ui", StaticFiles(directory=frontend_dir, html=True), name="ui")
```

- [ ] **Step 3: Smoke verify lifespan + sweep + boot**

```bash
uv run uvicorn main:app --port 8000 &
sleep 3
curl -s http://localhost:8000/health
kill %1
```
Expected: `{"status":"ok"}` and server logs show `Prompts loaded` and `Orphan sweep complete`.

- [ ] **Step 4: Commit**

```bash
git add main.py frontend/.gitkeep
git commit -m "feat: app lifespan with prompt warmup, orphan sweep, cors, /ui mount"
```

---

## Task 16: Frontend — shared CSS, JS helpers, upload page

**Files:**
- Create: `frontend/app.css`, `frontend/app.js`, `frontend/index.html`

> **Frontend safety rule:** All dynamic content from server SSE/JSON is rendered with `textContent` or `createElement`+attribute setters — never `innerHTML` with interpolated values. The helpers below enforce this.

- [ ] **Step 1: Create `frontend/app.css`**

```css
body { font-family: ui-sans-serif, system-ui, sans-serif; max-width: 720px;
       margin: 2rem auto; padding: 0 1rem; color: #111; }
h1 { font-size: 1.5rem; margin-bottom: 1rem; }
form { display: flex; flex-direction: column; gap: 0.75rem; }
label { font-weight: 500; }
select, input[type="file"], button {
    padding: 0.5rem 0.75rem; font-size: 1rem;
    border: 1px solid #ccc; border-radius: 6px; background: #fff;
}
button { background: #2563eb; color: #fff; border-color: #2563eb; cursor: pointer; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
.list { display: flex; flex-direction: column; gap: 0.5rem; margin-top: 1rem; }
.toc-item { padding: 0.6rem 0.8rem; border: 1px solid #e5e7eb; border-radius: 6px;
           cursor: pointer; }
.toc-item:hover { background: #f3f4f6; }
.phase { padding: 0.6rem 0.8rem; border: 1px solid #e5e7eb; border-radius: 6px;
         margin-top: 0.5rem; background: #f9fafb; }
.phase.done { border-color: #10b981; }
.phase.running { border-color: #f59e0b; }
.phase.error { border-color: #ef4444; background: #fef2f2; }
.phase-output { white-space: pre-wrap; word-wrap: break-word; font-size: 0.85rem;
                margin-top: 0.5rem; max-height: 240px; overflow: auto;
                background: #fff; padding: 0.5rem; border-radius: 4px; }
.muted { color: #6b7280; font-size: 0.875rem; }
.error-text { color: #ef4444; }
a.download { display: inline-block; margin-top: 1rem; padding: 0.5rem 0.9rem;
             background: #10b981; color: #fff; text-decoration: none; border-radius: 6px; }
```

- [ ] **Step 2: Create `frontend/app.js`**

```javascript
const SUBJECTS = [
    "biology", "english", "geometriya-g7-11", "history",
    "kimyo-g7-11", "math-algebra", "physics",
];

async function uploadBook(file, subject) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("subject", subject);
    const res = await fetch("/api/v1/books", { method: "POST", body: fd });
    if (!res.ok) throw new Error(`upload failed: ${res.status} ${await res.text()}`);
    return await res.json();
}

async function generate(bookId, sectionId) {
    const res = await fetch(
        `/api/v1/books/${bookId}/sections/${sectionId}/generate`,
        { method: "POST", headers: {"Content-Type": "application/json"}, body: "{}" }
    );
    if (!res.ok) throw new Error(`generate failed: ${res.status} ${await res.text()}`);
    return await res.json();
}

function streamSse(url, handlers) {
    const es = new EventSource(url);
    for (const [name, fn] of Object.entries(handlers)) {
        es.addEventListener(name, (e) => fn(JSON.parse(e.data)));
    }
    es.onerror = () => es.close();
    return es;
}

// Safe DOM helpers — never use innerHTML with server data
function el(tag, opts = {}) {
    const node = document.createElement(tag);
    if (opts.className) node.className = opts.className;
    if (opts.text != null) node.textContent = opts.text;
    if (opts.href != null) node.href = opts.href;
    if (opts.attrs) {
        for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
    }
    return node;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

window.SUBJECTS = SUBJECTS;
window.uploadBook = uploadBook;
window.generate = generate;
window.streamSse = streamSse;
window.el = el;
window.clear = clear;
```

- [ ] **Step 3: Create `frontend/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Edu-Homework — Upload</title>
<link rel="stylesheet" href="app.css">
</head>
<body>
<h1>Edu-Homework — Upload curriculum book</h1>
<form id="f">
  <label>Subject
    <select id="subject" required></select>
  </label>
  <label>PDF file
    <input id="file" type="file" accept="application/pdf" required>
  </label>
  <button id="submit" type="submit">Upload</button>
  <p class="muted" id="status"></p>
</form>

<script src="app.js"></script>
<script>
const sel = document.getElementById("subject");
for (const s of window.SUBJECTS) {
  sel.appendChild(window.el("option", {text: s, attrs: {value: s}}));
}

document.getElementById("f").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const btn = document.getElementById("submit");
  const status = document.getElementById("status");
  btn.disabled = true;
  status.textContent = "uploading…";
  try {
    const file = document.getElementById("file").files[0];
    const subject = sel.value;
    const book = await window.uploadBook(file, subject);
    location.href = `/ui/book.html?id=${encodeURIComponent(book.id)}`;
  } catch (e) {
    status.textContent = e.message;
    btn.disabled = false;
  }
});
</script>
</body>
</html>
```

- [ ] **Step 4: Smoke verify upload page renders**

```bash
uv run uvicorn main:app --port 8000 &
sleep 2
curl -s http://localhost:8000/ui/index.html | grep "Upload curriculum book"
kill %1
```
Expected: `<h1>Edu-Homework — Upload curriculum book</h1>`.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/app.css frontend/app.js
git commit -m "feat: frontend upload page (throwaway test ui)"
```

---

## Task 17: Frontend — book/TOC page with SSE

**Files:**
- Create: `frontend/book.html`

- [ ] **Step 1: Create `frontend/book.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Edu-Homework — Pick section</title>
<link rel="stylesheet" href="app.css">
</head>
<body>
<h1>Pick a section</h1>
<p class="muted" id="status">connecting…</p>
<div id="list" class="list"></div>

<script src="app.js"></script>
<script>
const params = new URLSearchParams(location.search);
const bookId = params.get("id");
if (!bookId) {
  const p = window.el("p", {text: "missing ?id="});
  document.body.appendChild(p);
  throw new Error("missing id");
}

const status = document.getElementById("status");
const list = document.getElementById("list");

function renderToc(entries) {
  window.clear(list);
  status.textContent = "click a section to generate homework";
  for (const e of entries) {
    const ch = e.chapter_title ? `${e.chapter_title} — ` : "";
    const pages = e.page_start ? ` (p.${e.page_start}-${e.page_end ?? "?"})` : "";
    const label = `${ch}${e.section_number} ${e.section_title}${pages}`;
    const div = window.el("div", {className: "toc-item", text: label});
    div.addEventListener("click", async () => {
      div.style.opacity = 0.5;
      try {
        const job = await window.generate(bookId, e.id);
        location.href = `/ui/job.html?id=${encodeURIComponent(job.id)}`;
      } catch (err) {
        status.textContent = err.message;
        div.style.opacity = 1;
      }
    });
    list.appendChild(div);
  }
}

window.streamSse(`/api/v1/books/${encodeURIComponent(bookId)}/toc/stream`, {
  status: (d) => status.textContent = `${d.status}…`,
  toc_ready: (d) => renderToc(d.entries),
  error: (d) => status.textContent = `error: ${d.message}`,
});
</script>
</body>
</html>
```

- [ ] **Step 2: Smoke verify**

```bash
uv run uvicorn main:app --port 8000 &
sleep 2
curl -s http://localhost:8000/ui/book.html | grep "Pick a section"
kill %1
```
Expected: `<h1>Pick a section</h1>`.

- [ ] **Step 3: Commit**

```bash
git add frontend/book.html
git commit -m "feat: frontend book/toc page with sse stream"
```

---

## Task 18: Frontend — job page with SSE + end-to-end smoke

**Files:**
- Create: `frontend/job.html`

- [ ] **Step 1: Create `frontend/job.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Edu-Homework — Generating</title>
<link rel="stylesheet" href="app.css">
</head>
<body>
<h1>Generating homework</h1>
<p class="muted" id="diff"></p>
<div id="phases" class="list"></div>
<p id="done"></p>

<script src="app.js"></script>
<script>
const params = new URLSearchParams(location.search);
const jobId = params.get("id");
if (!jobId) {
  document.body.appendChild(window.el("p", {text: "missing ?id="}));
  throw new Error("missing id");
}

const phases = document.getElementById("phases");
const diff = document.getElementById("diff");
const done = document.getElementById("done");
const phaseEls = new Map();

function ensurePhaseEl(name, order) {
  if (phaseEls.has(name)) return phaseEls.get(name);
  const wrap = window.el("div", {className: "phase running"});
  const title = window.el("strong", {text: `${order}. ${name}`});
  const status = window.el("div", {className: "muted", text: "running…"});
  wrap.appendChild(title);
  wrap.appendChild(status);
  phases.appendChild(wrap);
  phaseEls.set(name, {wrap, title, status, output: null});
  return phaseEls.get(name);
}

function setPhaseDone(d) {
  const cell = ensurePhaseEl(d.phase_name, d.phase_order);
  cell.wrap.className = "phase done";
  const tin = d.tokens_input ?? "?";
  const tout = d.tokens_output ?? "?";
  cell.status.textContent = `tokens in/out: ${tin}/${tout}`;
  if (cell.output) cell.wrap.removeChild(cell.output);
  const preview = (d.output_md || "").slice(0, 400) +
    ((d.output_md || "").length > 400 ? "…" : "");
  cell.output = window.el("pre", {className: "phase-output", text: preview});
  cell.wrap.appendChild(cell.output);
}

window.streamSse(`/api/v1/jobs/${encodeURIComponent(jobId)}/stream`, {
  phase_started: (d) => ensurePhaseEl(d.phase_name, d.phase_order),
  phase_completed: (d) => setPhaseDone(d),
  difficulty_classified: (d) =>
    diff.textContent = `Difficulty: ${String(d.difficulty).toUpperCase()}`,
  job_completed: (d) => {
    window.clear(done);
    const a = window.el("a", {
      className: "download",
      text: "Download homework .md",
      href: d.download_url,
      attrs: {download: ""},
    });
    done.appendChild(a);
  },
  error: (d) => {
    window.clear(done);
    done.appendChild(window.el("span", {
      className: "error-text",
      text: `error: ${d.message}`,
    }));
  },
});
</script>
</body>
</html>
```

- [ ] **Step 2: Commit the frontend**

```bash
git add frontend/job.html
git commit -m "feat: frontend job/generation page with sse stream and download"
```

- [ ] **Step 3: End-to-end smoke verification**

This is the single integration test for the slice. Requires a real Gemini API key in `.env` and a small curriculum PDF on hand.

```bash
docker compose up -d
uv run alembic upgrade head
uv run uvicorn main:app --reload --port 8000
```

Then in a browser: open `http://localhost:8000/ui/index.html`. Pick a subject. Upload a small PDF (≤ 10 pages is fastest for the smoke). Watch the TOC page fill in, click any section, watch phases progress on the job page. When `job_completed` fires, click the download link and verify the `.md` opens with the expected `## Preview`, `## Flashcards`, etc. headers.

If anything fails:
- Check `uvicorn` console logs for the failing phase.
- Hit `GET /api/v1/jobs/{id}` to inspect persisted state.
- Run `docker compose exec postgres psql -U edu -d edu_homework -c "SELECT id, status, current_phase, error_message FROM homework_jobs ORDER BY created_at DESC LIMIT 5;"` to see DB-side state.

- [ ] **Step 4: Final commit (any tweaks made during smoke)**

```bash
git add -A
git diff --cached --stat
git commit -m "fix: post-smoke adjustments" || true
```

---

## Self-review

Spec coverage scan:

| Spec section | Implemented in |
|---|---|
| §3 Architecture & module layout | Tasks 1, 7 (skeleton) + later tasks land files where the spec said |
| §4 `books` table | Task 3 (model), Task 4 (migration) |
| §4 `toc_entries` table | Task 3, Task 4 |
| §4 `homework_jobs` table | Task 3, Task 4 |
| §4 `phase_outputs` table (incl. unique `(job_id, phase_order)`) | Task 3, Task 4 |
| §4 idempotency: book by `(content_sha256, subject)` | Task 13 + repo `find_ready_by_hash` (Task 6) |
| §4 idempotency: job by `(book_id, toc_entry_id)` | Task 14 + repo `find_active_for_section` (Task 6) |
| §5 `GET /health` (root + v1) | Task 7, Task 15 |
| §5 `POST /api/v1/books` | Task 13 |
| §5 `GET /api/v1/books/{id}` | Task 13 |
| §5 `GET /api/v1/books/{id}/toc/stream` | Task 13 (with replay) |
| §5 `POST .../sections/{id}/generate` | Task 14 |
| §5 `GET /api/v1/jobs/{id}` | Task 14 |
| §5 `GET /api/v1/jobs/{id}/stream` | Task 14 (with replay) |
| §5 `GET /api/v1/jobs/{id}/download` | Task 14 |
| §5 SSE replay protocol | Task 13 + Task 14 (catch-up branches before subscribing) |
| §5 Background work via `asyncio.create_task` | Tasks 13, 14 |
| §5 Lifespan orphan sweep | Task 15 |
| §6 `extract` phase | Task 12 + Task 10 (`extract_lesson_context`) |
| §6 `classify` phase + difficulty resolution | Task 12 |
| §6 `SUBJECT_FLOWS` for all 7 subjects | Task 8 |
| §6 Prompt assembly (system + user + file_uri) | Task 10 (`run_phase_prompt`) |
| §6 Final assembly (skip `extract`/`classify`) | Task 12 (`_assemble`, `_INTERNAL_PHASES`) |
| §6 Failure model: phase fail → job fail, no retry | Task 12 |
| §7 Configuration env vars | Task 1 (`.env.example`) + Task 2 (`config.py`) |
| Auth stub | Task 7 |
| Throwaway UI under `/ui` | Tasks 15–18 |
| Tests | Intentionally absent (project feedback) |

Type / signature consistency check: repository function names referenced in services match (`set_status`, `set_difficulty`, `set_gemini_file`, `bulk_create`, `find_active_for_section`, `find_ready_by_hash`, `list_running_for_sweep`); SSE event names match between `events.py`, `pipeline.py` emits, the SSE replay branches in `books.py` / `jobs.py`, and the frontend handler maps in `book.html` / `job.html`.

Placeholder scan: no TBD/TODO/"implement later" remain. Every code step contains the actual code. The only narrative note is the engineer reminder at the top of Task 8 to verify per-subject phase lists against each `flow.md` — this is real cross-checking work, not a placeholder.

Frontend safety scan: `app.js` exposes `el()` / `clear()` helpers that use `createElement` + `textContent`; no `innerHTML` is used anywhere in the three HTML pages.

---

## Open follow-ups (post-thin-slice)

These are deferred per the spec's out-of-scope list and intentionally not in this plan:
- Real auth, multi-tenancy, persistent file storage, real job queue, multi-worker pub/sub, phase-level retry, per-subject extract prompts, conditional phase skipping in code, regenerate-single-phase, cost dashboard, webhooks, rate limiting, structured logging, metrics, frontend polish, Idempotency-Key header, tests.
