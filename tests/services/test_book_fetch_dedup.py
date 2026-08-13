"""Tests for r13-fetch-1: per-book_id lock de-dupes concurrent same-book fetches.

RED before the lock: N threads each call ensure_book_pdf_sync for the SAME
book_id → _fetch_to_temp is called N times (one per thread).
GREEN after the lock: the first thread fetches; the rest wait then hit the
cached fast path → _fetch_to_temp is called exactly ONCE.

The second half of this file covers `r13-fetch-2`, the documented residual of
that fix: the in-process locks de-dupe threads and coroutines but NOT separate
worker PROCESSES. Host-18 runs two worker processes, so it made two full
transfers of the same book. Those tests use REAL subprocesses and REAL second
file descriptors — an OS-level lock is only worth what the OS actually enforces,
and a mock of it would prove nothing.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from pathlib import Path

import pytest

from app.config import settings
from app.services import book_fetch, storage
from app.services.errors import BookFetchTimeout

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_fake_fetch(calls: list, content: bytes = b"%PDF-1.4 x"):
    """Return a _fetch_to_temp replacement that records each call and writes
    realistic bytes so the second waiter finds a valid cached file."""
    def fake_fetch(url: str, headers: dict, tmp: Path) -> None:
        calls.append(1)
        time.sleep(0.05)  # simulate I/O delay; ensures overlap between threads
        Path(tmp).write_bytes(content)
    return fake_fetch


# ---------------------------------------------------------------------------
# test 1: same book — download exactly once
# ---------------------------------------------------------------------------

def test_concurrent_same_book_fetches_download_once(monkeypatch, tmp_path):
    """N threads on the SAME book_id → _fetch_to_temp called exactly once."""
    monkeypatch.setattr("app.config.settings.var_dir", str(tmp_path))
    monkeypatch.setattr("app.config.settings.fleet_head_url", "http://head.local")
    monkeypatch.setattr("app.config.settings.auth_token", "")

    calls: list = []
    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _make_fake_fetch(calls))

    book_id = str(uuid.uuid4())
    n_threads = 5
    barrier = threading.Barrier(n_threads)
    results: list[Path] = [None] * n_threads
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        barrier.wait()  # all threads start together
        try:
            results[idx] = book_fetch.ensure_book_pdf_sync(book_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"threads raised: {errors}"
    assert len(calls) == 1, (
        f"expected 1 fetch but got {len(calls)} — lock is missing or not working"
    )
    for r in results:
        assert r is not None
        assert r.read_bytes() != b""


# ---------------------------------------------------------------------------
# test 2: distinct books — each fetches independently (lock must be per-book)
# ---------------------------------------------------------------------------

def test_distinct_books_each_fetch(monkeypatch, tmp_path):
    """Two different book_ids → _fetch_to_temp called exactly twice (one per book)."""
    monkeypatch.setattr("app.config.settings.var_dir", str(tmp_path))
    monkeypatch.setattr("app.config.settings.fleet_head_url", "http://head.local")
    monkeypatch.setattr("app.config.settings.auth_token", "")

    calls: list = []
    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _make_fake_fetch(calls))

    book_a = str(uuid.uuid4())
    book_b = str(uuid.uuid4())
    barrier = threading.Barrier(2)
    results: dict[str, Path] = {}
    errors: list[Exception] = []

    def worker(bid: str) -> None:
        barrier.wait()
        try:
            results[bid] = book_fetch.ensure_book_pdf_sync(bid)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(book_a,)),
        threading.Thread(target=worker, args=(book_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"threads raised: {errors}"
    assert len(calls) == 2, (
        f"expected 2 fetches (one per book) but got {len(calls)} — lock is global (wrong)"
    )
    for r in results.values():
        assert r is not None
        assert r.read_bytes() != b""


# ---------------------------------------------------------------------------
# test 3: the lock is keyed per book_id (deterministic — proves NOT a global lock)
# ---------------------------------------------------------------------------

def test_lock_for_is_per_book_identity():
    """Same book_id → same lock object; different book_ids → different locks.
    This deterministically proves the lock is per-book, not global (so distinct
    books never serialize on one shared lock) — the download-count test alone
    can't distinguish per-book from a global lock."""
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    assert book_fetch._lock_for(a) is book_fetch._lock_for(a)
    assert book_fetch._lock_for(a) is not book_fetch._lock_for(b)


# ===========================================================================
# r13-fetch-2 — CROSS-PROCESS dedup (the residual the in-process locks left)
#
# `_lock_for` / `_async_lock_for` live in one interpreter. A host running two
# worker processes therefore still pulled the same source.pdf twice. Everything
# below drives the real thing: separate OS processes, separate file
# descriptors, and the kernel's own lock — never a stand-in for it.
# ===========================================================================

_BODY = b"%PDF-1.4 the one and only transfer of this book\n"

_CHILD = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path

    sys.path.insert(0, os.environ["REPO_ROOT"])
    from app.services import book_fetch

    BODY = {body!r}
    counter = Path(os.environ["FETCH_COUNTER"])

    def _instrumented(url, headers, tmp):
        # O_APPEND of a single byte: the cross-process-safe way to count.
        with open(counter, "ab") as fh:
            fh.write(b"x")
            fh.flush()
            os.fsync(fh.fileno())
        time.sleep(float(os.environ["FETCH_SECONDS"]))
        Path(tmp).write_bytes(BODY)

    book_fetch._fetch_to_temp = _instrumented

    Path(os.environ["READY_FILE"]).write_text("ready")
    go = Path(os.environ["GO_FILE"])
    deadline = time.monotonic() + 60
    while not go.exists():
        if time.monotonic() > deadline:
            raise SystemExit("go file never appeared")
        time.sleep(0.01)

    path = book_fetch.ensure_book_pdf_sync(os.environ["BOOK_ID"], expected_size=len(BODY))
    sys.stdout.write(str(path))
    """
).format(body=_BODY)


def _child_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        REPO_ROOT=str(_REPO_ROOT),
        VAR_DIR=str(tmp_path),
        FLEET_HEAD_URL="http://head.local:8000",
        AUTH_TOKEN="",
        **extra,
    )
    return env


def _wait_for(predicate, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


# ── (a) two real processes on one host download the book exactly once ────────

def test_two_worker_processes_on_one_host_download_the_book_once(tmp_path):
    """THE residual, reproduced for real: two separate python processes (what
    Host-18 actually runs) each call ensure_book_pdf_sync for the same book at
    the same moment.

    RED (in-process locks only): each process has its own `_book_locks` dict, so
    both download → the counter file holds 2 bytes, i.e. 2x237 MB over one relay
    hop. GREEN (filesystem lock next to the cache file): the loser blocks on the
    kernel lock, then re-checks and finds the finished file → 1 byte.
    """
    book_id = str(uuid.uuid4())
    counter = tmp_path / "fetches"
    counter.write_bytes(b"")
    go = tmp_path / "go"
    n = 3

    procs, ready_files = [], []
    for i in range(n):
        ready = tmp_path / f"ready-{i}"
        ready_files.append(ready)
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", _CHILD],
                cwd=str(_REPO_ROOT),
                env=_child_env(
                    tmp_path,
                    BOOK_ID=book_id,
                    FETCH_COUNTER=str(counter),
                    FETCH_SECONDS="1.0",
                    READY_FILE=str(ready),
                    GO_FILE=str(go),
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    try:
        _wait_for(lambda: all(r.exists() for r in ready_files), 90, "children to boot")
        go.write_text("go")  # release all three into the fetch together
        outs = [p.communicate(timeout=180) for p in procs]
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()

    for proc, (out, err) in zip(procs, outs):
        assert proc.returncode == 0, f"child failed:\nstdout={out}\nstderr={err}"

    assert counter.stat().st_size == 1, (
        f"{counter.stat().st_size} of {n} processes downloaded the book — the "
        f"dedup is still per-process, so a two-worker host still makes N transfers"
    )
    pdf = tmp_path / "books" / book_id / "source.pdf"
    assert pdf.read_bytes() == _BODY
    assert list(pdf.parent.glob("*.tmp")) == [], "a temp leaked"
    for out, _err in outs:
        assert out == str(pdf), out


# ── (a') the waiter RE-CHECKS after the lock — the entire point ──────────────

def test_waiter_rechecks_the_cache_after_the_lock_and_skips_the_download(
    monkeypatch, tmp_path
):
    """A real other process holds the lock, finishes the download while we wait,
    and releases. We must notice the file appeared and NOT re-download it.

    Without the post-acquire re-check the waiter would wake up, walk straight
    into the fetch and pull the whole book a second time — the wait would have
    bought nothing.
    """
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head.local:8000")
    monkeypatch.setattr(settings, "auth_token", "")

    book_id = str(uuid.uuid4())
    book_dir = storage.book_dir(book_id)
    book_dir.mkdir(parents=True, exist_ok=True)
    holding = tmp_path / "holding"
    body = b"%PDF-1.4 written by the OTHER process while we waited\n"

    holder_src = textwrap.dedent(
        """
        import os, sys, time
        from pathlib import Path
        sys.path.insert(0, os.environ["REPO_ROOT"])
        from app.services import book_fetch

        book_id = os.environ["BOOK_ID"]
        with book_fetch._host_lock(book_id, None, time.monotonic() + 30):
            Path(os.environ["HOLDING_FILE"]).write_text("held")
            time.sleep(1.0)
            # finish "our" download exactly the way the real path does
            d = book_fetch.storage.book_dir(book_id)
            tmp = d / "source.pdf.holder.tmp"
            tmp.write_bytes({body!r})
            os.replace(tmp, d / "source.pdf")
        """
    ).format(body=body)

    holder = subprocess.Popen(
        [sys.executable, "-c", holder_src],
        cwd=str(_REPO_ROOT),
        env=_child_env(tmp_path, BOOK_ID=book_id, HOLDING_FILE=str(holding)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for(lambda: holding.exists(), 90, "the other process to take the lock")

        def _must_not_fetch(*_a, **_k):
            raise AssertionError(
                "re-downloaded a book another process had already finished — "
                "the post-acquire re-check is missing"
            )

        monkeypatch.setattr(book_fetch, "_fetch_to_temp", _must_not_fetch)
        started = time.monotonic()
        got = book_fetch.ensure_book_pdf_sync(book_id, expected_size=len(body))
        waited = time.monotonic() - started
    finally:
        try:
            out, err = holder.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            holder.kill()
            out, err = holder.communicate(timeout=60)

    assert holder.returncode == 0, f"holder failed:\nstdout={out}\nstderr={err}"
    assert got == storage.book_pdf_path(book_id)
    assert got.read_bytes() == body
    assert waited > 0.5, (
        f"returned in {waited:.2f}s — it never actually blocked on the other "
        f"process's lock"
    )


# ── (b) a partial / temp download is never mistaken for a cache hit ──────────

def test_partial_temp_file_is_not_treated_as_a_cache_hit(monkeypatch, tmp_path):
    """An interrupted transfer leaves `source.pdf.<pid>.<hex>.tmp` behind. That
    file must never satisfy a later caller: bytes only become `source.pdf` via
    the atomic rename at the very end."""
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head.local:8000")
    monkeypatch.setattr(settings, "auth_token", "")

    book_id = str(uuid.uuid4())
    book_dir = storage.book_dir(book_id)
    book_dir.mkdir(parents=True, exist_ok=True)
    stale = book_dir / f"source.pdf.{os.getpid()}.deadbeef.tmp"
    stale.write_bytes(b"%PDF-1.4 half a boo")  # an abandoned partial download

    seen_targets: list[Path] = []

    def _transfer(url, headers, tmp):
        seen_targets.append(Path(tmp))
        # mid-transfer the real file must still not exist
        assert not storage.book_pdf_path(book_id).exists(), (
            "a partially written body was visible at source.pdf"
        )
        Path(tmp).write_bytes(_BODY)

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _transfer)
    got = book_fetch.ensure_book_pdf_sync(book_id, expected_size=len(_BODY))

    assert len(seen_targets) == 1, "the stale partial suppressed the real download"
    assert seen_targets[0] != got, "downloaded straight onto the cache path"
    assert seen_targets[0].name.endswith(".tmp")
    assert got.read_bytes() == _BODY
    assert stale.exists(), "unrelated temps are not ours to delete"


def test_the_lock_file_itself_is_not_mistaken_for_the_book(monkeypatch, tmp_path):
    """The lock lives next to the cache file. It must not be mistaken for the
    PDF, and it must not be handed back to a caller."""
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head.local:8000")
    monkeypatch.setattr(settings, "auth_token", "")

    book_id = str(uuid.uuid4())
    calls: list = []

    def _transfer(url, headers, tmp):
        calls.append(1)
        Path(tmp).write_bytes(_BODY)

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _transfer)
    got = book_fetch.ensure_book_pdf_sync(book_id, expected_size=len(_BODY))

    lock_path = book_fetch._host_lock_path(book_id)
    assert lock_path.exists(), "no lock file was created next to the cache file"
    assert lock_path != got
    assert got.name == "source.pdf"
    assert len(calls) == 1

    # ...and it must not turn the NEXT call into a cache miss
    calls.clear()
    again = book_fetch.ensure_book_pdf_sync(book_id, expected_size=len(_BODY))
    assert again == got
    assert calls == [], "the lock file broke the cached fast path"


# ── (c) the lock is really released when the holder finishes ─────────────────

def _acquired_on_a_fresh_fd(book_id) -> bool:
    """Try the OS lock from a SECOND file descriptor. Both flock and Windows
    byte-range locks are per-open-file-handle, so this is a real, independent
    contender even inside one process — not a bookkeeping check."""
    fd = os.open(book_fetch._host_lock_path(book_id), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not book_fetch._try_lock(fd):
            return False
        book_fetch._unlock(fd)
        return True
    finally:
        os.close(fd)


def test_host_lock_blocks_a_second_fd_and_frees_it_on_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    book_id = str(uuid.uuid4())
    storage.book_dir(book_id).mkdir(parents=True, exist_ok=True)

    assert _acquired_on_a_fresh_fd(book_id), "lock was busy before anyone took it"
    with book_fetch._host_lock(book_id, None, time.monotonic() + 30):
        assert not _acquired_on_a_fresh_fd(book_id), (
            "a second fd took the lock while it was held — it is not a real "
            "OS-level lock"
        )
    assert _acquired_on_a_fresh_fd(book_id), "the lock was not released on exit"


def test_host_lock_is_released_when_the_fetch_finishes_and_when_it_fails(
    monkeypatch, tmp_path
):
    """Both exits matter: a lock leaked on the error path wedges the book for
    every later job on the host until the process dies."""
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head.local:8000")
    monkeypatch.setattr(settings, "auth_token", "")

    ok_book = str(uuid.uuid4())
    monkeypatch.setattr(
        book_fetch, "_fetch_to_temp", lambda u, h, tmp: Path(tmp).write_bytes(_BODY)
    )
    book_fetch.ensure_book_pdf_sync(ok_book, expected_size=len(_BODY))
    assert _acquired_on_a_fresh_fd(ok_book), "lock held after a successful fetch"

    bad_book = str(uuid.uuid4())

    def _boom(*_a, **_k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(book_fetch, "_fetch_to_temp", _boom)
    with pytest.raises(RuntimeError, match="fetch from head failed"):
        book_fetch.ensure_book_pdf_sync(bad_book)
    assert _acquired_on_a_fresh_fd(bad_book), "lock held after a failed fetch"


def test_lock_survives_the_holder_being_killed(monkeypatch, tmp_path):
    """The no-deadlock property, proved rather than asserted: SIGKILL a process
    mid-hold (no unwinding, no cleanup code runs) and the lock must still be
    free. This is why the choice is an OS lock the kernel drops on fd close /
    process death, and not a lockfile whose existence means 'held' — that one
    strands the book forever the first time a worker is killed."""
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    book_id = str(uuid.uuid4())
    storage.book_dir(book_id).mkdir(parents=True, exist_ok=True)
    holding = tmp_path / "holding"

    src = textwrap.dedent(
        """
        import os, sys, time
        from pathlib import Path
        sys.path.insert(0, os.environ["REPO_ROOT"])
        from app.services import book_fetch
        with book_fetch._host_lock(os.environ["BOOK_ID"], None, time.monotonic() + 30):
            Path(os.environ["HOLDING_FILE"]).write_text("held")
            time.sleep(120)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", src],
        cwd=str(_REPO_ROOT),
        env=_child_env(tmp_path, BOOK_ID=book_id, HOLDING_FILE=str(holding)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for(lambda: holding.exists(), 90, "the child to take the lock")
        assert not _acquired_on_a_fresh_fd(book_id), "child never really held it"
        proc.kill()
        proc.wait(timeout=60)
        _wait_for(
            lambda: _acquired_on_a_fresh_fd(book_id),
            30,
            "the kernel to drop a killed holder's lock",
        )
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=60)


# ── the wait is bounded: a wedged holder cannot park a worker forever ────────

def test_waiting_on_another_process_is_bounded_by_the_fetch_budget(
    monkeypatch, tmp_path
):
    """A holder that never finishes must cost the waiter its fetch budget, not
    its job. Same contract as the in-process wait, one layer out."""
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fleet_head_url", "http://head.local:8000")
    monkeypatch.setattr(settings, "auth_token", "")
    monkeypatch.setattr(settings, "book_fetch_timeout_seconds", 1)

    book_id = str(uuid.uuid4())
    storage.book_dir(book_id).mkdir(parents=True, exist_ok=True)
    holding = tmp_path / "holding"

    src = textwrap.dedent(
        """
        import os, sys, time
        from pathlib import Path
        sys.path.insert(0, os.environ["REPO_ROOT"])
        from app.services import book_fetch
        with book_fetch._host_lock(os.environ["BOOK_ID"], None, time.monotonic() + 60):
            Path(os.environ["HOLDING_FILE"]).write_text("held")
            time.sleep(60)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", src],
        cwd=str(_REPO_ROOT),
        env=_child_env(tmp_path, BOOK_ID=book_id, HOLDING_FILE=str(holding)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for(lambda: holding.exists(), 90, "the child to take the lock")

        def _must_not_fetch(*_a, **_k):
            raise AssertionError("fetched while another process held the book")

        monkeypatch.setattr(book_fetch, "_fetch_to_temp", _must_not_fetch)
        started = time.monotonic()
        with pytest.raises(BookFetchTimeout) as excinfo:
            book_fetch.ensure_book_pdf_sync(book_id, expected_size=248_725_094)
        elapsed = time.monotonic() - started
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=60)

    assert elapsed < 20, f"waited {elapsed:.1f}s on a 1s budget"
    message = str(excinfo.value)
    assert "another process on this host" in message, message
    assert "BOOK_FETCH_TIMEOUT_SECONDS" in message, message
    assert str(book_id) in message, message
    assert "237.2 MB" in message, message
