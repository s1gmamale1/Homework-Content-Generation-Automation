from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app import config
from app.services import sa_key_vault, storage


def _point_vault(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    monkeypatch.setattr(config.settings, "var_dir", str(root))
    return storage.sa_key_dir()


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _seed_uuid_file(body: bytes = b"credential") -> tuple[UUID, bytes]:
    return uuid4(), body


@pytest.mark.skipif(os.name == "nt", reason="numeric modes are POSIX")
def test_harden_preserves_six_keys_active_and_temp_bytes(monkeypatch, tmp_path):
    vault = _point_vault(monkeypatch, tmp_path)
    vault.mkdir(mode=0o777)
    paths = [storage.sa_key_path(uuid4()) for _ in range(6)]
    paths += [storage.sa_key_active_path(), vault / ".active.json.crash.tmp"]
    for index, path in enumerate(paths):
        path.write_bytes(f"private-{index}".encode())
        path.chmod(0o666)
    vault.chmod(0o777)
    before = {path.name: _sha(path.read_bytes()) for path in paths}

    sa_key_vault.harden_vault()

    assert stat.S_IMODE(vault.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths)
    assert before == {path.name: _sha(path.read_bytes()) for path in paths}


@pytest.mark.skipif(os.name == "nt", reason="numeric modes are POSIX")
def test_every_public_operation_creates_a_private_missing_vault(monkeypatch, tmp_path):
    key_id = uuid4()
    sha = _sha(b"missing")
    ticket = sa_key_vault.DeleteQuarantine(
        key_id=key_id,
        sha256=sha,
        original_name=f"{key_id}.json",
        quarantine_name=f".{key_id}.json.{sha}.{'a' * 32}.delete-quarantine",
    )
    calls = [
        lambda: sa_key_vault.harden_vault(),
        lambda: sa_key_vault.atomic_write(storage.sa_key_active_path(), b"x"),
        lambda: sa_key_vault.file_present(storage.sa_key_active_path()),
        lambda: sa_key_vault.read_bytes(storage.sa_key_path(key_id)),
        lambda: sa_key_vault.remove(storage.sa_key_path(key_id), missing_ok=True),
        lambda: sa_key_vault.quarantine_for_delete(
            storage.sa_key_path(key_id), expected_sha256=sha
        ),
        lambda: sa_key_vault.restore_quarantined_delete(ticket),
        lambda: sa_key_vault.discard_quarantined_delete(ticket),
        lambda: sa_key_vault.reconcile_delete_quarantines({}),
        lambda: sa_key_vault.snapshot_uuid_inventory(),
        lambda: sa_key_vault.verify_uuid_inventory({}),
    ]

    for index, call in enumerate(calls):
        root = tmp_path / str(index)
        vault = _point_vault(monkeypatch, root)
        try:
            call()
        except (FileNotFoundError, sa_key_vault.SAKeyVaultError):
            pass
        assert stat.S_IMODE(vault.stat().st_mode) == 0o700


def test_file_present_distinguishes_safe_file_from_proven_absence(monkeypatch, tmp_path):
    _point_vault(monkeypatch, tmp_path)
    path = storage.sa_key_active_path()

    assert sa_key_vault.file_present(path) is False
    sa_key_vault.atomic_write(path, b"credential")
    assert sa_key_vault.file_present(path) is True


@pytest.mark.skipif(os.name == "nt", reason="numeric modes are POSIX")
def test_atomic_write_temp_is_private_before_publication(monkeypatch, tmp_path):
    _point_vault(monkeypatch, tmp_path)
    destination = storage.sa_key_active_path()
    observed: list[int] = []
    real_replace = sa_key_vault._replace_write_through

    def inspect_then_replace(source, target, *, vault_fd=None):
        info = os.stat(source.name, dir_fd=vault_fd, follow_symlinks=False)
        observed.append(stat.S_IMODE(info.st_mode))
        return real_replace(source, target, vault_fd=vault_fd)

    monkeypatch.setattr(sa_key_vault, "_replace_write_through", inspect_then_replace)
    sa_key_vault.atomic_write(destination, b"secret")

    assert observed == [0o600]
    assert destination.read_bytes() == b"secret"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_atomic_failure_keeps_old_destination_and_cleans_temp(monkeypatch, tmp_path):
    _point_vault(monkeypatch, tmp_path)
    destination = storage.sa_key_active_path()
    sa_key_vault.atomic_write(destination, b"old")

    def fail_replace(*_args, **_kwargs):
        raise OSError("boom")

    monkeypatch.setattr(sa_key_vault, "_replace_write_through", fail_replace)
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.atomic_write(destination, b"new")

    assert destination.read_bytes() == b"old"
    assert list(destination.parent.glob("*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="fsync fd behavior is POSIX")
def test_successful_write_fsyncs_file_and_held_directory(monkeypatch, tmp_path):
    vault = _point_vault(monkeypatch, tmp_path)
    destination = storage.sa_key_active_path()
    real_fsync = os.fsync
    synced: list[tuple[bool, int]] = []

    def tracked_fsync(fd: int):
        info = os.fstat(fd)
        synced.append((stat.S_ISDIR(info.st_mode), fd))
        return real_fsync(fd)

    monkeypatch.setattr(sa_key_vault.os, "fsync", tracked_fsync)
    sa_key_vault.atomic_write(destination, b"durable")

    assert any(not is_directory for is_directory, _fd in synced)
    assert any(is_directory for is_directory, _fd in synced)
    assert vault.is_dir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX hostile path fixtures")
def test_symlink_vault_is_rejected_without_touching_target(monkeypatch, tmp_path):
    target = tmp_path / "outside"
    target.mkdir(mode=0o755)
    marker = target / "marker"
    marker.write_bytes(b"outside")
    vault = _point_vault(monkeypatch, tmp_path)
    vault.symlink_to(target, target_is_directory=True)

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.harden_vault()

    assert marker.read_bytes() == b"outside"
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


@pytest.mark.skipif(os.name == "nt", reason="POSIX hostile path fixtures")
@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink"])
def test_unsafe_destination_is_rejected_without_touching_target(
    monkeypatch, tmp_path, kind
):
    vault = _point_vault(monkeypatch, tmp_path)
    vault.mkdir()
    destination = storage.sa_key_active_path()
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    if kind == "symlink":
        destination.symlink_to(outside)
    elif kind == "fifo":
        os.mkfifo(destination)
    else:
        os.link(outside, destination)

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.atomic_write(destination, b"new")

    assert outside.read_bytes() == b"outside"


def test_paths_outside_the_vault_are_rejected(monkeypatch, tmp_path):
    _point_vault(monkeypatch, tmp_path / "var")
    outside = tmp_path / "outside.json"
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.atomic_write(outside, b"secret")
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.read_bytes(outside)
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.remove(outside)
    disguised = storage.sa_key_dir() / "nested" / ".." / "active.json"
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.atomic_write(disguised, b"secret")


@pytest.mark.skipif(os.name == "nt", reason="POSIX link-count races")
@pytest.mark.parametrize("timing", ["before", "after"])
def test_atomic_write_rejects_temp_hardlinks_before_and_after_publication(
    monkeypatch, tmp_path, timing
):
    vault = _point_vault(monkeypatch, tmp_path)
    destination = storage.sa_key_active_path()
    sa_key_vault.atomic_write(destination, b"old")
    injected = vault / f"{timing}.link"
    if timing == "before":
        def link_before(name: str, _file_fd: int, vault_fd: int):
            os.link(name, injected.name, src_dir_fd=vault_fd, dst_dir_fd=vault_fd)

        monkeypatch.setattr(sa_key_vault, "_posix_before_publish", link_before)
    else:
        real_replace = sa_key_vault._replace_write_through

        def link_after(source, target, *, vault_fd=None):
            real_replace(source, target, vault_fd=vault_fd)
            os.link(target.name, injected.name, src_dir_fd=vault_fd, dst_dir_fd=vault_fd)

        monkeypatch.setattr(sa_key_vault, "_replace_write_through", link_after)

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.atomic_write(destination, b"new")
    assert injected.exists()


@pytest.mark.skipif(os.name == "nt", reason="dir-fd anchoring is POSIX")
@pytest.mark.parametrize("operation", ["read", "write", "remove", "quarantine"])
def test_posix_operations_remain_anchored_when_vault_path_is_swapped(
    monkeypatch, tmp_path, operation
):
    vault = _point_vault(monkeypatch, tmp_path)
    vault.mkdir()
    parked = tmp_path / "original-vault"
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    path.write_bytes(body)
    path.chmod(0o600)
    fired = False

    def swap(label: str, _fd: int):
        nonlocal fired
        if fired or label != operation:
            return
        fired = True
        vault.rename(parked)
        vault.mkdir(mode=0o700)
        (vault / path.name).write_bytes(b"replacement")
        (vault / path.name).chmod(0o600)

    monkeypatch.setattr(sa_key_vault, "_posix_after_vault_open", swap)
    if operation == "read":
        assert sa_key_vault.read_bytes(path) == body
        assert (vault / path.name).read_bytes() == b"replacement"
    elif operation == "write":
        sa_key_vault.atomic_write(path, b"new-original")
        assert (parked / path.name).read_bytes() == b"new-original"
        assert (vault / path.name).read_bytes() == b"replacement"
    elif operation == "remove":
        sa_key_vault.remove(path)
        assert not (parked / path.name).exists()
        assert (vault / path.name).read_bytes() == b"replacement"
    else:
        ticket = sa_key_vault.quarantine_for_delete(
            path, expected_sha256=_sha(body)
        )
        assert not (parked / path.name).exists()
        assert (parked / ticket.quarantine_name).read_bytes() == body
        assert (vault / path.name).read_bytes() == b"replacement"
    assert fired


@pytest.mark.skipif(os.name == "nt", reason="dir-fd anchoring is POSIX")
@pytest.mark.parametrize("operation", ["restore", "discard"])
def test_quarantine_finish_remains_anchored_when_vault_path_is_swapped(
    monkeypatch, tmp_path, operation
):
    vault = _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    sa_key_vault.atomic_write(storage.sa_key_path(key_id), body)
    ticket = sa_key_vault.quarantine_for_delete(
        storage.sa_key_path(key_id), expected_sha256=_sha(body)
    )
    parked = tmp_path / "original-vault"
    fired = False

    def swap(label: str, _fd: int):
        nonlocal fired
        if fired or label != operation:
            return
        fired = True
        vault.rename(parked)
        vault.mkdir(mode=0o700)
        replacement = vault / ticket.quarantine_name
        replacement.write_bytes(b"replacement")
        replacement.chmod(0o600)

    monkeypatch.setattr(sa_key_vault, "_posix_after_vault_open", swap)
    if operation == "restore":
        sa_key_vault.restore_quarantined_delete(ticket)
        assert (parked / ticket.original_name).read_bytes() == body
    else:
        sa_key_vault.discard_quarantined_delete(ticket)
        assert not (parked / ticket.quarantine_name).exists()
    assert (vault / ticket.quarantine_name).read_bytes() == b"replacement"
    assert fired


def test_quarantine_requires_canonical_uuid_filename_and_matching_sha(monkeypatch, tmp_path):
    vault = _point_vault(monkeypatch, tmp_path)
    body = b"credential"
    non_uuid = vault / "active.json"
    sa_key_vault.atomic_write(non_uuid, body)
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.quarantine_for_delete(non_uuid, expected_sha256=_sha(body))

    key_id = uuid4()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.quarantine_for_delete(path, expected_sha256="0" * 64)
    assert path.read_bytes() == body

    ticket = sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    assert ticket.key_id == key_id
    assert ticket.original_name == f"{key_id}.json"
    assert ticket.sha256 == _sha(body)
    assert ticket.quarantine_name.startswith(f".{key_id}.json.{_sha(body)}.")
    assert ticket.quarantine_name.endswith(".delete-quarantine")


def test_restore_never_replaces_a_different_canonical_file(monkeypatch, tmp_path):
    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    ticket = sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    sa_key_vault.atomic_write(path, b"different")

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.restore_quarantined_delete(ticket)

    assert path.read_bytes() == b"different"
    assert (path.parent / ticket.quarantine_name).read_bytes() == body


@pytest.mark.skipif(os.name == "nt", reason="dir-fd publication is POSIX")
def test_restore_canonical_absent_never_replaces_a_concurrent_creation(
    monkeypatch, tmp_path
):
    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    ticket = sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    quarantine = path.parent / ticket.quarantine_name
    replacement = b"concurrent-canonical"
    real_link = os.link
    real_open = os.open
    fired = False

    def create_canonical_before_link(
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        nonlocal fired
        fired = True
        replacement_fd = real_open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=dst_dir_fd,
        )
        try:
            os.write(replacement_fd, replacement)
        finally:
            os.close(replacement_fd)
        return real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(sa_key_vault.os, "link", create_canonical_before_link)

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.restore_quarantined_delete(ticket)

    assert fired
    assert path.read_bytes() == replacement
    assert quarantine.read_bytes() == body


@pytest.mark.skipif(os.name == "nt", reason="dir-fd publication is POSIX")
def test_restore_recovers_after_crash_between_link_and_quarantine_unlink(
    monkeypatch, tmp_path
):
    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    ticket = sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    quarantine = path.parent / ticket.quarantine_name
    real_unlink = os.unlink
    fired = False

    def crash_before_quarantine_unlink(name, *, dir_fd=None):
        nonlocal fired
        if not fired and name == ticket.quarantine_name:
            fired = True
            raise OSError("simulated crash after no-replace publication")
        return real_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(sa_key_vault.os, "unlink", crash_before_quarantine_unlink)

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.restore_quarantined_delete(ticket)

    assert fired
    assert path.read_bytes() == body
    assert quarantine.read_bytes() == body
    assert path.stat().st_ino == quarantine.stat().st_ino
    assert path.stat().st_nlink == 2

    sa_key_vault.restore_quarantined_delete(ticket)

    assert path.read_bytes() == body
    assert path.stat().st_nlink == 1
    assert not quarantine.exists()


@pytest.mark.skipif(os.name == "nt", reason="dir-fd publication is POSIX")
@pytest.mark.parametrize("db_present", [True, False])
def test_plain_harden_then_reconcile_recovers_a_crashed_restore_link(
    monkeypatch, tmp_path, db_present
):
    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    ticket = sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    quarantine = path.parent / ticket.quarantine_name
    real_unlink = os.unlink

    def crash_before_quarantine_unlink(name, *, dir_fd=None):
        if name == ticket.quarantine_name:
            raise OSError("simulated crash after no-replace publication")
        return real_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(sa_key_vault.os, "unlink", crash_before_quarantine_unlink)
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.restore_quarantined_delete(ticket)
    monkeypatch.setattr(sa_key_vault.os, "unlink", real_unlink)

    assert path.stat().st_ino == quarantine.stat().st_ino
    assert path.stat().st_nlink == quarantine.stat().st_nlink == 2

    # Startup keeps this ordering: harden before the DB-backed reconciliation.
    sa_key_vault.harden_vault()
    expected = {str(key_id): _sha(body)} if db_present else {}
    sa_key_vault.reconcile_delete_quarantines(expected)
    sa_key_vault.verify_uuid_inventory(expected)

    assert not quarantine.exists()
    if db_present:
        assert path.read_bytes() == body
        assert path.stat().st_nlink == 1
    else:
        assert not path.exists()


@pytest.mark.skipif(os.name == "nt", reason="hard-link recovery is POSIX")
@pytest.mark.parametrize(
    "invalid_pair",
    ["generic", "malformed", "wrong-hash", "wrong-uuid", "third-link"],
)
def test_harden_rejects_every_noncanonical_two_link_recovery_pair(
    monkeypatch, tmp_path, invalid_pair
):
    vault = _point_vault(monkeypatch, tmp_path)
    vault.mkdir(mode=0o700)
    key_id, body = _seed_uuid_file()
    canonical = storage.sa_key_path(key_id)
    canonical.write_bytes(body)
    canonical.chmod(0o640)
    encoded_id = key_id
    encoded_sha = _sha(body)
    alias = vault / "generic-hard-link"

    if invalid_pair in {"generic", "malformed"}:
        pass
    elif invalid_pair == "wrong-hash":
        encoded_sha = _sha(b"different")
    elif invalid_pair == "wrong-uuid":
        encoded_id = uuid4()

    if invalid_pair == "generic":
        os.link(canonical, alias)
    else:
        quarantine_name = (
            f".{encoded_id}.json.{encoded_sha}.{'a' * 32}.delete-quarantine"
        )
        if invalid_pair == "malformed":
            quarantine_name = f".{encoded_id}.json.{encoded_sha}.bad.delete-quarantine"
        quarantine = vault / quarantine_name
        os.link(canonical, quarantine)
        if invalid_pair == "third-link":
            os.link(canonical, alias)

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.harden_vault()
    assert stat.S_IMODE(canonical.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name == "nt", reason="hard-link recovery is POSIX")
def test_harden_makes_an_exact_crashed_restore_pair_private(monkeypatch, tmp_path):
    vault = _point_vault(monkeypatch, tmp_path)
    vault.mkdir(mode=0o700)
    key_id, body = _seed_uuid_file()
    canonical = storage.sa_key_path(key_id)
    canonical.write_bytes(body)
    canonical.chmod(0o640)
    quarantine = vault / (
        f".{key_id}.json.{_sha(body)}.{'a' * 32}.delete-quarantine"
    )
    os.link(canonical, quarantine)

    sa_key_vault.harden_vault()

    assert canonical.stat().st_ino == quarantine.stat().st_ino
    assert canonical.stat().st_nlink == quarantine.stat().st_nlink == 2
    assert stat.S_IMODE(canonical.stat().st_mode) == 0o600
    assert stat.S_IMODE(quarantine.stat().st_mode) == 0o600


def test_inventory_does_not_hide_a_db_absent_canonical_orphan(monkeypatch, tmp_path):
    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    sa_key_vault.atomic_write(storage.sa_key_path(key_id), body)

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.verify_uuid_inventory({})


def test_restore_discards_only_a_byte_identical_duplicate(monkeypatch, tmp_path):
    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    ticket = sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    sa_key_vault.atomic_write(path, body)

    sa_key_vault.restore_quarantined_delete(ticket)

    assert path.read_bytes() == body
    assert not (path.parent / ticket.quarantine_name).exists()


@pytest.mark.skipif(os.name == "nt", reason="dir-fd anchoring is POSIX")
def test_restore_keeps_quarantine_when_duplicate_canonical_name_is_swapped(
    monkeypatch, tmp_path
):
    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    ticket = sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    sa_key_vault.atomic_write(path, body)
    moved = path.parent / "canonical-held.json"
    replacement = b"replacement"
    fired = False

    def swap(
        canonical_name: str,
        _canonical_fd: int,
        _quarantine_name: str,
        _quarantine_fd: int,
        vault_fd: int,
    ) -> None:
        nonlocal fired
        fired = True
        os.rename(canonical_name, moved.name, src_dir_fd=vault_fd, dst_dir_fd=vault_fd)
        replacement_fd = os.open(
            canonical_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=vault_fd,
        )
        try:
            os.write(replacement_fd, replacement)
        finally:
            os.close(replacement_fd)

    monkeypatch.setattr(
        sa_key_vault,
        "_posix_before_duplicate_restore_delete",
        swap,
        raising=False,
    )

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.restore_quarantined_delete(ticket)

    assert fired
    assert path.read_bytes() == replacement
    assert moved.read_bytes() == body
    assert (path.parent / ticket.quarantine_name).read_bytes() == body


def test_discard_rejects_tampered_ticket_or_bytes(monkeypatch, tmp_path):
    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    ticket = sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    quarantine = path.parent / ticket.quarantine_name
    quarantine.write_bytes(b"tampered")

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.discard_quarantined_delete(ticket)
    assert quarantine.exists()


def test_reconcile_restores_present_rows_and_discards_absent_rows(monkeypatch, tmp_path):
    _point_vault(monkeypatch, tmp_path)
    restore_id, restore_body = _seed_uuid_file(b"restore")
    discard_id, discard_body = _seed_uuid_file(b"discard")
    for key_id, body in ((restore_id, restore_body), (discard_id, discard_body)):
        sa_key_vault.atomic_write(storage.sa_key_path(key_id), body)
    restore_ticket = sa_key_vault.quarantine_for_delete(
        storage.sa_key_path(restore_id), expected_sha256=_sha(restore_body)
    )
    discard_ticket = sa_key_vault.quarantine_for_delete(
        storage.sa_key_path(discard_id), expected_sha256=_sha(discard_body)
    )

    sa_key_vault.reconcile_delete_quarantines({str(restore_id): _sha(restore_body)})

    assert storage.sa_key_path(restore_id).read_bytes() == restore_body
    assert not (storage.sa_key_dir() / restore_ticket.quarantine_name).exists()
    assert not (storage.sa_key_dir() / discard_ticket.quarantine_name).exists()


def test_reconcile_fails_closed_on_multiple_or_mismatched_tickets(monkeypatch, tmp_path):
    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    ticket = sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    original = path.parent / ticket.quarantine_name
    duplicate_name = (
        f".{key_id}.json.{_sha(body)}.{'b' * 32}.delete-quarantine"
    )
    duplicate = path.parent / duplicate_name
    duplicate.write_bytes(body)
    duplicate.chmod(0o600)
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.reconcile_delete_quarantines({str(key_id): _sha(body)})
    duplicate.unlink()
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.reconcile_delete_quarantines({str(key_id): "f" * 64})
    assert original.exists()


def test_delete_quarantine_ticket_is_frozen_and_exact(monkeypatch, tmp_path):
    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    ticket = sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    with pytest.raises((AttributeError, TypeError)):
        ticket.sha256 = "0" * 64
    forged = sa_key_vault.DeleteQuarantine(
        key_id=ticket.key_id,
        sha256=ticket.sha256,
        original_name="active.json",
        quarantine_name=ticket.quarantine_name,
    )
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.restore_quarantined_delete(forged)


def test_inventory_accepts_exact_uuid_files_active_and_stale_temps(monkeypatch, tmp_path):
    vault = _point_vault(monkeypatch, tmp_path)
    expected: dict[str, str] = {}
    for index in range(6):
        key_id = uuid4()
        body = f"key-{index}".encode()
        sa_key_vault.atomic_write(storage.sa_key_path(key_id), body)
        expected[str(key_id)] = _sha(body)
    sa_key_vault.atomic_write(storage.sa_key_active_path(), b"active")
    stale = vault / ".active.json.crash.tmp"
    stale.write_bytes(b"stale")
    if os.name != "nt":
        stale.chmod(0o600)

    sa_key_vault.verify_uuid_inventory(expected)


@pytest.mark.skipif(os.name == "nt", reason="POSIX fd anchoring assertion")
def test_snapshot_inventory_uses_vault_fd_and_never_path_reads(
    monkeypatch, tmp_path
):
    _point_vault(monkeypatch, tmp_path)
    expected: dict[str, str] = {}
    for index in range(2):
        key_id = uuid4()
        body = f"key-{index}".encode()
        sa_key_vault.atomic_write(storage.sa_key_path(key_id), body)
        expected[str(key_id)] = _sha(body)

    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda *_args, **_kwargs: pytest.fail("path read bypass"),
    )
    monkeypatch.setattr(
        Path,
        "iterdir",
        lambda *_args, **_kwargs: pytest.fail("path iteration bypass"),
    )

    assert sa_key_vault.snapshot_uuid_inventory() == expected


def test_snapshot_inventory_fails_closed_on_unsafe_entry(monkeypatch, tmp_path):
    vault = _point_vault(monkeypatch, tmp_path)
    sa_key_vault.atomic_write(storage.sa_key_path(uuid4()), b"credential")
    unsafe = vault / "notes.txt"
    unsafe.write_bytes(b"not a key")
    if os.name != "nt":
        unsafe.chmod(0o600)

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.snapshot_uuid_inventory()


@pytest.mark.parametrize(
    "failure", ["missing", "mismatch", "orphan", "unexpected", "fake-temp"]
)
def test_inventory_fails_closed_on_any_difference(monkeypatch, tmp_path, failure):
    vault = _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    expected = {str(key_id): _sha(body)}
    if failure == "missing":
        path.unlink()
    elif failure == "mismatch":
        path.write_bytes(b"different")
    elif failure == "orphan":
        other = uuid4()
        sa_key_vault.atomic_write(storage.sa_key_path(other), b"orphan")
    elif failure == "unexpected":
        extra = vault / "notes.txt"
        extra.write_bytes(b"unexpected")
        if os.name != "nt":
            extra.chmod(0o600)
    else:
        extra = vault / ".notes.txt.crash.tmp"
        extra.write_bytes(b"unexpected")
        if os.name != "nt":
            extra.chmod(0o600)

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.verify_uuid_inventory(expected)


def test_inventory_rejects_unresolved_delete_quarantine(monkeypatch, tmp_path):
    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.verify_uuid_inventory({})


@pytest.mark.skipif(os.name != "nt", reason="real Windows security descriptor acceptance")
def test_windows_hardening_installs_one_protected_current_sid_ace(monkeypatch, tmp_path):
    import win32con
    import win32security

    vault = _point_vault(monkeypatch, tmp_path)
    vault.mkdir()
    path = storage.sa_key_path(uuid4())
    path.write_bytes(b"credential")
    everyone = win32security.CreateWellKnownSid(win32security.WinWorldSid, None)
    for target in (vault, path):
        descriptor = win32security.GetFileSecurity(
            str(target), win32security.DACL_SECURITY_INFORMATION
        )
        dacl = descriptor.GetSecurityDescriptorDacl() or win32security.ACL()
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, everyone)
        descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
        win32security.SetFileSecurity(
            str(target), win32security.DACL_SECURITY_INFORMATION, descriptor
        )

    sa_key_vault.harden_vault()

    for target, directory in ((vault, True), (path, False)):
        with sa_key_vault._open_windows_handle(
            target,
            use=sa_key_vault._WindowsHandleUse.READ_OR_HASH,
            directory=directory,
            disposition=win32con.OPEN_EXISTING,
        ) as handle:
            sa_key_vault._verify_private_windows_dacl(handle, directory=directory)
        if directory:
            probe = target / "probe"
            probe.write_bytes(b"rw")
            assert probe.read_bytes() == b"rw"
        else:
            assert sa_key_vault.read_bytes(target) == b"credential"


@pytest.mark.skipif(os.name != "nt", reason="real Windows held-handle acceptance")
def test_windows_production_operations_use_exact_handle_profiles(monkeypatch, tmp_path):
    import win32con

    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    calls: list[sa_key_vault._WindowsHandleUse] = []
    create_calls: list[tuple[int, int, int, int]] = []
    flushes: list[object] = []
    real_open = sa_key_vault._open_windows_handle
    real_create = sa_key_vault.win32file.CreateFile
    real_flush = sa_key_vault.win32file.FlushFileBuffers

    def tracked_open(path, *, use, directory, disposition):
        calls.append(use)
        return real_open(path, use=use, directory=directory, disposition=disposition)

    def tracked_create(
        filename, access, share, attributes, disposition, flags, template
    ):
        create_calls.append((access, share, disposition, flags))
        return real_create(
            filename, access, share, attributes, disposition, flags, template
        )

    def tracked_flush(handle):
        flushes.append(handle)
        return real_flush(handle)

    monkeypatch.setattr(sa_key_vault, "_open_windows_handle", tracked_open)
    monkeypatch.setattr(sa_key_vault.win32file, "CreateFile", tracked_create)
    monkeypatch.setattr(sa_key_vault.win32file, "FlushFileBuffers", tracked_flush)
    monkeypatch.setattr(Path, "open", lambda *_a, **_kw: pytest.fail("path reopen"))
    monkeypatch.setattr(Path, "read_bytes", lambda *_a, **_kw: pytest.fail("path read"))
    monkeypatch.setattr(Path, "write_bytes", lambda *_a, **_kw: pytest.fail("path write"))
    sa_key_vault.atomic_write(path, body)
    assert sa_key_vault.read_bytes(path) == body
    ticket = sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    sa_key_vault.restore_quarantined_delete(ticket)
    assert sa_key_vault._WindowsHandleUse.TEMP_READ_WRITE in calls
    assert sa_key_vault._WindowsHandleUse.READ_OR_HASH in calls
    assert [access for access, _share, _disposition, _flags in create_calls] == [
        sa_key_vault._windows_desired_access(use) for use in calls
    ]
    assert all(
        share == sa_key_vault._WINDOWS_SHARE_ALL
        for _access, share, _disposition, _flags in create_calls
    )
    assert all(
        flags & sa_key_vault.win32file.FILE_FLAG_OPEN_REPARSE_POINT
        for _access, _share, _disposition, flags in create_calls
    )
    assert flushes


@pytest.mark.skipif(os.name != "nt", reason="real Windows hostile path acceptance")
def test_windows_symlink_junction_and_hardlink_are_rejected(monkeypatch, tmp_path):
    vault = _point_vault(monkeypatch, tmp_path)
    vault.mkdir()
    outside_file = tmp_path / "outside.json"
    outside_file.write_bytes(b"outside")
    symlink = storage.sa_key_active_path()
    os.symlink(outside_file, symlink)
    for operation in (
        sa_key_vault.harden_vault,
        lambda: sa_key_vault.read_bytes(symlink),
        lambda: sa_key_vault.atomic_write(symlink, b"new"),
        lambda: sa_key_vault.remove(symlink),
    ):
        with pytest.raises(sa_key_vault.SAKeyVaultError):
            operation()
    symlink.unlink()
    hardlink = storage.sa_key_active_path()
    os.link(outside_file, hardlink)
    for operation in (
        sa_key_vault.harden_vault,
        lambda: sa_key_vault.read_bytes(hardlink),
        lambda: sa_key_vault.atomic_write(hardlink, b"new"),
        lambda: sa_key_vault.remove(hardlink),
    ):
        with pytest.raises(sa_key_vault.SAKeyVaultError):
            operation()
    hardlink.unlink()
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    junction = tmp_path / "junction-root" / "sa_keys"
    junction.parent.mkdir()
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside_dir)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(config.settings, "var_dir", str(junction.parent))
    junction_child = storage.sa_key_active_path()
    for operation in (
        sa_key_vault.harden_vault,
        lambda: sa_key_vault.read_bytes(junction_child),
        lambda: sa_key_vault.atomic_write(junction_child, b"new"),
        lambda: sa_key_vault.remove(junction_child),
    ):
        with pytest.raises(sa_key_vault.SAKeyVaultError):
            operation()
    assert outside_file.read_bytes() == b"outside"


@pytest.mark.skipif(os.name != "nt", reason="real Windows no-fallback acceptance")
def test_windows_access_denied_never_falls_back_to_path_io(monkeypatch, tmp_path):
    import winerror

    _point_vault(monkeypatch, tmp_path)
    path = storage.sa_key_path(uuid4())

    def denied(*_args, **_kwargs):
        error = OSError("denied")
        error.winerror = winerror.ERROR_ACCESS_DENIED
        raise error

    monkeypatch.setattr(sa_key_vault.win32file, "CreateFile", denied)
    monkeypatch.setattr(Path, "open", lambda *_a, **_kw: pytest.fail("path fallback"))
    monkeypatch.setattr(Path, "read_bytes", lambda *_a, **_kw: pytest.fail("path fallback"))
    monkeypatch.setattr(Path, "write_bytes", lambda *_a, **_kw: pytest.fail("path fallback"))
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.read_bytes(path)


@pytest.mark.skipif(os.name != "nt", reason="real Windows missing-ok denial acceptance")
def test_windows_remove_missing_ok_never_treats_access_denied_as_absent(
    monkeypatch, tmp_path
):
    import pywintypes
    import winerror

    vault = _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)

    def denied_delete(*_args, **_kwargs):
        raise pywintypes.error(
            winerror.ERROR_ACCESS_DENIED, "DeleteFile", "Access is denied."
        )

    monkeypatch.setattr(sa_key_vault.win32file, "DeleteFile", denied_delete)
    # Restore the global Path probe before pytest formats a failure or tears
    # down tmp_path; both pytest internals legitimately call Path.exists().
    with monkeypatch.context() as path_probe:
        path_probe.setattr(
            Path,
            "exists",
            lambda *_args, **_kwargs: pytest.fail("Path.exists fail-open"),
        )
        with pytest.raises(sa_key_vault.SAKeyVaultError):
            sa_key_vault._remove_windows_verified(
                vault, path.name, missing_ok=True
            )
    assert path.read_bytes() == body


@pytest.mark.skipif(os.name != "nt", reason="real Windows inventory anchoring acceptance")
def test_windows_inventory_rejects_vault_name_swap_even_with_identical_bytes(
    monkeypatch, tmp_path
):
    vault = _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    parked = tmp_path / "inventory-held-vault"
    fired = False

    def swap(_vault: Path, _name: str, _vault_handle) -> None:
        nonlocal fired
        if fired:
            return
        fired = True
        vault.rename(parked)
        vault.mkdir()
        (vault / path.name).write_bytes(body)

    monkeypatch.setattr(
        sa_key_vault, "_windows_before_inventory_child", swap, raising=False
    )

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.verify_uuid_inventory({str(key_id): _sha(body)})

    assert fired
    assert (parked / path.name).read_bytes() == body
    assert (vault / path.name).read_bytes() == body


@pytest.mark.skipif(os.name != "nt", reason="real Windows publication identity acceptance")
def test_windows_atomic_write_rejects_destination_name_swap_after_move(
    monkeypatch, tmp_path
):
    vault = _point_vault(monkeypatch, tmp_path)
    path = storage.sa_key_active_path()
    sa_key_vault.atomic_write(path, b"old")
    moved = vault / "published-held.json"
    replacement = b"replacement"
    real_replace = sa_key_vault._replace_write_through
    fired = False

    def swap_after_move(source, destination, *, vault_fd=None):
        nonlocal fired
        real_replace(source, destination, vault_fd=vault_fd)
        fired = True
        destination.rename(moved)
        destination.write_bytes(replacement)

    monkeypatch.setattr(sa_key_vault, "_replace_write_through", swap_after_move)

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.atomic_write(path, b"new")

    assert fired
    assert moved.read_bytes() == b"new"
    assert path.read_bytes() == replacement


@pytest.mark.skipif(os.name != "nt", reason="real Windows duplicate restore acceptance")
def test_windows_restore_keeps_quarantine_when_duplicate_canonical_is_swapped(
    monkeypatch, tmp_path
):
    _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    ticket = sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
    sa_key_vault.atomic_write(path, body)
    moved = path.parent / "duplicate-held.json"
    replacement = b"replacement"
    fired = False

    def swap(label: str, _candidate: Path, _handle) -> None:
        nonlocal fired
        if fired or label != "restore-discard-duplicate":
            return
        fired = True
        path.rename(moved)
        path.write_bytes(replacement)

    monkeypatch.setattr(sa_key_vault, "_windows_before_path_mutation", swap)

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.restore_quarantined_delete(ticket)

    assert fired
    assert path.read_bytes() == replacement
    assert moved.read_bytes() == body
    assert (path.parent / ticket.quarantine_name).read_bytes() == body


@pytest.mark.skipif(os.name != "nt", reason="real Windows handle anchoring acceptance")
def test_windows_acl_application_stays_on_validated_handle_after_name_swap(
    monkeypatch, tmp_path
):
    import win32con
    import win32security

    vault = _point_vault(monkeypatch, tmp_path)
    vault.mkdir()
    path = storage.sa_key_path(uuid4())
    path.write_bytes(b"original")
    moved = vault / "moved.json"
    replacement_body = b"replacement"
    fired = False

    def swap(opened_path: Path, _handle, *, directory: bool):
        nonlocal fired
        if fired or directory or opened_path != path:
            return
        fired = True
        path.rename(moved)
        path.write_bytes(replacement_body)

    monkeypatch.setattr(sa_key_vault, "_windows_after_validation", swap)
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        sa_key_vault.harden_vault()
    assert fired

    with sa_key_vault._open_windows_handle(
        moved,
        use=sa_key_vault._WindowsHandleUse.ACL_ONLY,
        directory=False,
        disposition=win32con.OPEN_EXISTING,
    ) as original_handle:
        sa_key_vault._verify_private_windows_dacl(original_handle, directory=False)
    with sa_key_vault._open_windows_handle(
        path,
        use=sa_key_vault._WindowsHandleUse.ACL_ONLY,
        directory=False,
        disposition=win32con.OPEN_EXISTING,
    ) as replacement_handle:
        descriptor = win32security.GetSecurityInfo(
            replacement_handle,
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION,
        )
        control, _revision = descriptor.GetSecurityDescriptorControl()
        dacl = descriptor.GetSecurityDescriptorDacl()
        assert not control & win32security.SE_DACL_PROTECTED or (
            dacl is None or dacl.GetAceCount() != 1
        )
    assert path.read_bytes() == replacement_body


@pytest.mark.skipif(os.name != "nt", reason="real Windows path-mutation race acceptance")
@pytest.mark.parametrize("operation", ["quarantine", "remove", "restore", "discard"])
def test_windows_path_mutations_reject_a_swapped_child_name(
    monkeypatch, tmp_path, operation
):
    vault = _point_vault(monkeypatch, tmp_path)
    key_id, body = _seed_uuid_file()
    path = storage.sa_key_path(key_id)
    sa_key_vault.atomic_write(path, body)
    ticket = None
    if operation in {"restore", "discard"}:
        ticket = sa_key_vault.quarantine_for_delete(
            path, expected_sha256=_sha(body)
        )
        mutation_path = vault / ticket.quarantine_name
    else:
        mutation_path = path
    moved = vault / f"{operation}.held"
    replacement = b"replacement"
    fired = False

    def swap(label: str, candidate: Path, _handle):
        nonlocal fired
        if fired or label != operation:
            return
        fired = True
        candidate.rename(moved)
        candidate.write_bytes(replacement)

    monkeypatch.setattr(sa_key_vault, "_windows_before_path_mutation", swap)
    with pytest.raises(sa_key_vault.SAKeyVaultError):
        if operation == "quarantine":
            sa_key_vault.quarantine_for_delete(path, expected_sha256=_sha(body))
        elif operation == "remove":
            sa_key_vault.remove(path)
        elif operation == "restore":
            sa_key_vault.restore_quarantined_delete(ticket)
        else:
            sa_key_vault.discard_quarantined_delete(ticket)
    assert fired
    assert mutation_path.read_bytes() == replacement
    assert moved.read_bytes() == body
