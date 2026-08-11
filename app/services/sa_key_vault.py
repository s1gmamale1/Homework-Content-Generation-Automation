"""Private, crash-safe service-account credential vault primitives.

POSIX operations stay anchored to one verified directory file descriptor.  Windows
ACL application and byte I/O stay anchored to verified ``CreateFileW`` handles;
atomic publication is path-based only after the protected vault identity is checked
immediately before and after ``MoveFileExW``.  An administrator or a malicious
process already running as the same Windows SID is outside this boundary because
that principal can read or change the credential regardless.

Errors intentionally never include credential bytes or unsafe path material.
"""
from __future__ import annotations

import contextlib
import ctypes
import enum
import hashlib
import os
import re
import stat
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from app.services import storage

if os.name == "nt":  # pragma: no cover - imported and exercised on Windows CI
    import ntsecuritycon
    import win32api
    import win32con
    import win32file
    import win32security


_IS_WINDOWS = os.name == "nt"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_FILE_RE = re.compile(
    r"^(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.json$"
)
_QUARANTINE_RE = re.compile(
    r"^\.(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"\.json\.(?P<sha>[0-9a-f]{64})\.(?P<nonce>[0-9a-f]{32})\.delete-quarantine$"
)
_WRITE_TEMP_RE = re.compile(
    r"^\.(?:active\.json|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.json)"
    r"\.[A-Za-z0-9_-]+\.tmp$"
)


class SAKeyVaultError(RuntimeError):
    """A vault path or operation is unsafe; never includes file contents."""


class _WindowsPathNotFound(SAKeyVaultError):
    """A verified Windows API reported that the requested name is absent."""


@dataclass(frozen=True)
class DeleteQuarantine:
    key_id: UUID
    sha256: str
    original_name: str
    quarantine_name: str


class _WindowsHandleUse(enum.Enum):
    ACL_ONLY = "acl-only"
    READ_OR_HASH = "read-or-hash"
    TEMP_READ_WRITE = "temp-read-write"
    HANDLE_DELETE_OR_RENAME = "handle-delete-or-rename"


if _IS_WINDOWS:  # pragma: no cover - values are exercised on Windows CI
    _WINDOWS_COMMON_ACCESS = (
        win32con.READ_CONTROL
        | win32con.WRITE_DAC
        | ntsecuritycon.FILE_READ_ATTRIBUTES
    )
    _WINDOWS_SHARE_ALL = (
        win32con.FILE_SHARE_READ
        | win32con.FILE_SHARE_WRITE
        | win32con.FILE_SHARE_DELETE
    )
else:
    _WINDOWS_COMMON_ACCESS = 0
    _WINDOWS_SHARE_ALL = 0


def _posix_after_vault_open(_operation: str, _vault_fd: int) -> None:
    """Deterministic race-test seam; production deliberately does nothing."""


def _windows_after_validation(
    _path: Path, _handle: object, *, directory: bool
) -> None:  # pragma: no cover - real Windows race seam
    """Deterministic held-handle race-test seam; production does nothing."""


def _posix_before_publish(_name: str, _file_fd: int, _vault_fd: int) -> None:
    """Deterministic pre-publication race-test seam; production does nothing."""


def _windows_before_path_mutation(
    _operation: str, _path: Path, _handle: object
) -> None:  # pragma: no cover - real Windows race seam
    """Pause after held-handle validation but before the final name check."""


def _windows_before_inventory_child(
    _vault: Path, _name: str, _vault_handle: object
) -> None:  # pragma: no cover - real Windows race seam
    """Pause while the verified vault handle is held, before opening a child."""


def _posix_before_duplicate_restore_delete(
    _canonical_name: str,
    _canonical_fd: int,
    _quarantine_name: str,
    _quarantine_fd: int,
    _vault_fd: int,
) -> None:
    """Pause before identical duplicate evidence is discarded."""


def _raise_vault_error() -> SAKeyVaultError:
    return SAKeyVaultError("SA-key vault operation failed closed")


def _assert_direct_child(path: Path) -> tuple[Path, str]:
    path = Path(path)
    vault = storage.sa_key_dir()
    if path.parent != vault or path.name in {"", ".", ".."}:
        raise SAKeyVaultError("SA-key path is outside the vault")
    try:
        path_absolute = Path(os.path.abspath(os.fspath(path)))
        vault_absolute = Path(os.path.abspath(os.fspath(vault)))
    except (OSError, TypeError, ValueError) as exc:
        raise _raise_vault_error() from exc
    if (
        path_absolute.parent != vault_absolute
        or path.name != path_absolute.name
    ):
        raise SAKeyVaultError("SA-key path is outside the vault")
    return vault_absolute, path.name


def _reject_unsafe_stat(
    info: os.stat_result,
    *,
    directory: bool,
    allowed_file_links: tuple[int, ...] = (1,),
) -> None:
    reparse = bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if reparse or stat.S_ISLNK(info.st_mode) or not expected:
        raise SAKeyVaultError("SA-key vault contains an unsafe path type")
    if not directory and info.st_nlink not in allowed_file_links:
        raise SAKeyVaultError("SA-key vault file has multiple hard links")


def _create_vault_directory() -> Path:
    vault = Path(os.path.abspath(os.fspath(storage.sa_key_dir())))
    try:
        vault.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise _raise_vault_error() from exc
    return vault


@contextlib.contextmanager
def _open_posix_vault_fd(operation: str = "internal") -> Iterator[int]:
    vault = _create_vault_directory()
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(vault, flags)
    except OSError as exc:
        raise _raise_vault_error() from exc
    try:
        _reject_unsafe_stat(os.fstat(fd), directory=True)
        os.fchmod(fd, 0o700)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            raise _raise_vault_error()
        _posix_after_vault_open(operation, fd)
        yield fd
    except SAKeyVaultError:
        raise
    except OSError as exc:
        raise _raise_vault_error() from exc
    finally:
        os.close(fd)


def _posix_open_child(
    vault_fd: int,
    name: str,
    *,
    writable: bool = False,
    create_exclusive: bool = False,
    allowed_file_links: tuple[int, ...] = (1,),
) -> int:
    flags = getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_RDWR if writable else os.O_RDONLY
    if not writable:
        flags |= getattr(os, "O_NONBLOCK", 0)
    if create_exclusive:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(name, flags, 0o600, dir_fd=vault_fd)
        info = os.fstat(fd)
        _reject_unsafe_stat(
            info,
            directory=False,
            allowed_file_links=allowed_file_links,
        )
        return fd
    except SAKeyVaultError:
        if "fd" in locals():
            os.close(fd)
        raise
    except OSError as exc:
        if "fd" in locals():
            os.close(fd)
        raise _raise_vault_error() from exc


def _posix_verify_named_identity(
    vault_fd: int,
    name: str,
    held_fd: int,
    *,
    allowed_file_links: tuple[int, ...] = (1,),
) -> None:
    probe = _posix_open_child(
        vault_fd,
        name,
        allowed_file_links=allowed_file_links,
    )
    try:
        held = os.fstat(held_fd)
        named = os.fstat(probe)
        if (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino):
            raise _raise_vault_error()
    finally:
        os.close(probe)


def _windows_desired_access(use: _WindowsHandleUse) -> int:
    access = _WINDOWS_COMMON_ACCESS
    if not _IS_WINDOWS:
        return access
    if use is _WindowsHandleUse.READ_OR_HASH:
        access |= win32con.GENERIC_READ
    elif use is _WindowsHandleUse.TEMP_READ_WRITE:
        access |= win32con.GENERIC_READ | win32con.GENERIC_WRITE
    elif use is _WindowsHandleUse.HANDLE_DELETE_OR_RENAME:
        access |= win32con.DELETE
    return access


def _windows_information(handle):  # pragma: no cover - Windows CI
    information = win32file.GetFileInformationByHandle(handle)
    attributes = information[0]
    return {
        "attributes": attributes,
        "volume": information[4],
        "links": information[7],
        "index": (information[8], information[9]),
    }


def _windows_identity(handle) -> tuple[int, int, int]:  # pragma: no cover
    information = _windows_information(handle)
    return information["volume"], *information["index"]


def _windows_validate_handle(handle, *, directory: bool) -> None:  # pragma: no cover
    information = _windows_information(handle)
    attributes = information["attributes"]
    if attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT:
        raise SAKeyVaultError("SA-key vault contains an unsafe path type")
    is_directory = bool(attributes & win32con.FILE_ATTRIBUTE_DIRECTORY)
    if is_directory != directory:
        raise SAKeyVaultError("SA-key vault contains an unsafe path type")
    if not directory and information["links"] != 1:
        raise SAKeyVaultError("SA-key vault file has multiple hard links")


@contextlib.contextmanager
def _open_windows_handle(
    path: Path,
    *,
    use: _WindowsHandleUse,
    directory: bool,
    disposition: int,
):  # pragma: no cover - Windows CI
    flags = win32file.FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= win32file.FILE_FLAG_BACKUP_SEMANTICS
    try:
        handle = win32file.CreateFile(
            str(path),
            _windows_desired_access(use),
            _WINDOWS_SHARE_ALL,
            None,
            disposition,
            flags,
            None,
        )
    except OSError as exc:
        if getattr(exc, "winerror", None) in {2, 3}:
            raise _WindowsPathNotFound(
                "SA-key vault path was not found"
            ) from exc
        raise _raise_vault_error() from exc
    try:
        _windows_validate_handle(handle, directory=directory)
        yield handle
    except SAKeyVaultError:
        raise
    except OSError as exc:
        raise _raise_vault_error() from exc
    finally:
        handle.Close()


def _windows_process_sid():  # pragma: no cover - Windows CI
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    sid, _attributes = win32security.GetTokenInformation(
        token, win32security.TokenUser
    )
    return sid


def _set_private_windows_dacl(handle, *, directory: bool) -> None:  # pragma: no cover
    sid = _windows_process_sid()
    inheritance = 0
    if directory:
        inheritance = (
            win32security.OBJECT_INHERIT_ACE
            | win32security.CONTAINER_INHERIT_ACE
        )
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION_DS,
        inheritance,
        win32con.FILE_ALL_ACCESS,
        sid,
    )
    win32security.SetSecurityInfo(
        handle,
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )


def _verify_private_windows_dacl(handle, *, directory: bool) -> None:  # pragma: no cover
    descriptor = win32security.GetSecurityInfo(
        handle,
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
    )
    control, _revision = descriptor.GetSecurityDescriptorControl()
    if not control & win32security.SE_DACL_PROTECTED:
        raise _raise_vault_error()
    dacl = descriptor.GetSecurityDescriptorDacl()
    if dacl is None or dacl.GetAceCount() != 1:
        raise _raise_vault_error()
    ace = dacl.GetAce(0)
    (ace_type, ace_flags), access_mask, ace_sid = ace
    expected_flags = 0
    if directory:
        expected_flags = (
            win32security.OBJECT_INHERIT_ACE
            | win32security.CONTAINER_INHERIT_ACE
        )
    if (
        ace_type != win32security.ACCESS_ALLOWED_ACE_TYPE
        or ace_flags != expected_flags
        or access_mask != win32con.FILE_ALL_ACCESS
        or not win32security.EqualSid(ace_sid, _windows_process_sid())
    ):
        raise _raise_vault_error()


@contextlib.contextmanager
def _open_windows_vault(operation: str = "internal"):  # pragma: no cover
    vault = _create_vault_directory()
    with _open_windows_handle(
        vault,
        use=_WindowsHandleUse.ACL_ONLY,
        directory=True,
        disposition=win32con.OPEN_EXISTING,
    ) as handle:
        _windows_after_validation(vault, handle, directory=True)
        _set_private_windows_dacl(handle, directory=True)
        _verify_private_windows_dacl(handle, directory=True)
        yield vault, handle, _windows_identity(handle)


def _windows_recheck_vault_identity(vault: Path, expected) -> None:  # pragma: no cover
    with _open_windows_handle(
        vault,
        use=_WindowsHandleUse.ACL_ONLY,
        directory=True,
        disposition=win32con.OPEN_EXISTING,
    ) as probe:
        if _windows_identity(probe) != expected:
            raise _raise_vault_error()
        _verify_private_windows_dacl(probe, directory=True)


def _windows_require_named_identity(path: Path, held_handle) -> None:  # pragma: no cover
    with _open_windows_handle(
        path,
        use=_WindowsHandleUse.ACL_ONLY,
        directory=False,
        disposition=win32con.OPEN_EXISTING,
    ) as named_handle:
        if _windows_identity(named_handle) != _windows_identity(held_handle):
            raise _raise_vault_error()


def _windows_hash_handle(handle) -> str:  # pragma: no cover
    return hashlib.sha256(_windows_read_handle(handle)).hexdigest()


def _windows_harden_file(path: Path) -> None:  # pragma: no cover
    with _open_windows_handle(
        path,
        use=_WindowsHandleUse.ACL_ONLY,
        directory=False,
        disposition=win32con.OPEN_EXISTING,
    ) as handle:
        _windows_after_validation(path, handle, directory=False)
        _set_private_windows_dacl(handle, directory=False)
        _verify_private_windows_dacl(handle, directory=False)
        _windows_require_named_identity(path, handle)


def _posix_validate_crashed_restore_pair(
    vault_fd: int,
    ticket: DeleteQuarantine,
    *,
    harden_permissions: bool = False,
) -> bool:
    """Return whether two names are the exact two-link restore crash state."""
    quarantine_digest, quarantine_fd = _hash_posix_child(
        vault_fd,
        ticket.quarantine_name,
        allowed_file_links=(1, 2),
    )
    try:
        canonical_digest, canonical_fd = _hash_posix_child(
            vault_fd,
            ticket.original_name,
            allowed_file_links=(1, 2),
        )
        try:
            quarantine_info = os.fstat(quarantine_fd)
            canonical_info = os.fstat(canonical_fd)
            same_identity = (
                quarantine_info.st_dev,
                quarantine_info.st_ino,
            ) == (canonical_info.st_dev, canonical_info.st_ino)
            if not same_identity:
                if quarantine_info.st_nlink == canonical_info.st_nlink == 1:
                    return False
                raise _raise_vault_error()
            if (
                quarantine_info.st_nlink != 2
                or canonical_info.st_nlink != 2
                or quarantine_digest != ticket.sha256
                or canonical_digest != ticket.sha256
            ):
                raise _raise_vault_error()
            if harden_permissions:
                os.fchmod(quarantine_fd, 0o600)
                quarantine_info = os.fstat(quarantine_fd)
                canonical_info = os.fstat(canonical_fd)
            if (
                stat.S_IMODE(quarantine_info.st_mode) != 0o600
                or stat.S_IMODE(canonical_info.st_mode) != 0o600
            ):
                raise _raise_vault_error()
            _posix_verify_named_identity(
                vault_fd,
                ticket.quarantine_name,
                quarantine_fd,
                allowed_file_links=(2,),
            )
            _posix_verify_named_identity(
                vault_fd,
                ticket.original_name,
                canonical_fd,
                allowed_file_links=(2,),
            )
            return True
        finally:
            os.close(canonical_fd)
    finally:
        os.close(quarantine_fd)


def _posix_crashed_restore_names(vault_fd: int, names: list[str]) -> set[str]:
    """Identify only canonical UUID/quarantine pairs left by restore publication."""
    present = set(names)
    recovery_names: set[str] = set()
    for name in names:
        if not name.endswith(".delete-quarantine"):
            continue
        ticket = _ticket_from_name(name)
        if ticket.original_name not in present:
            continue
        if _posix_validate_crashed_restore_pair(
            vault_fd,
            ticket,
            harden_permissions=True,
        ):
            recovery_names.update((ticket.original_name, ticket.quarantine_name))
    return recovery_names


def harden_vault(*, _restore_ticket: DeleteQuarantine | None = None) -> None:
    if _IS_WINDOWS:  # pragma: no cover - Windows CI
        with _open_windows_vault("harden") as (vault, _handle, identity):
            try:
                _windows_recheck_vault_identity(vault, identity)
                entries = list(vault.iterdir())
                _windows_recheck_vault_identity(vault, identity)
            except OSError as exc:
                raise _raise_vault_error() from exc
            for path in entries:
                _windows_recheck_vault_identity(vault, identity)
                _windows_harden_file(path)
            _windows_recheck_vault_identity(vault, identity)
        return

    with _open_posix_vault_fd("harden") as vault_fd:
        try:
            names = os.listdir(vault_fd)
        except OSError as exc:
            raise _raise_vault_error() from exc
        crashed_restore_names = _posix_crashed_restore_names(vault_fd, names)
        restore_names = set()
        if _restore_ticket is not None:
            restore_names = {
                _restore_ticket.original_name,
                _restore_ticket.quarantine_name,
            }
        restore_stats: dict[str, os.stat_result] = {}
        for name in names:
            allowed_links = (
                (1, 2)
                if name in restore_names or name in crashed_restore_names
                else (1,)
            )
            fd = _posix_open_child(
                vault_fd,
                name,
                allowed_file_links=allowed_links,
            )
            try:
                if name not in crashed_restore_names:
                    os.fchmod(fd, 0o600)
                info = os.fstat(fd)
                _reject_unsafe_stat(
                    info,
                    directory=False,
                    allowed_file_links=allowed_links,
                )
                if stat.S_IMODE(info.st_mode) != 0o600:
                    raise _raise_vault_error()
                if name in restore_names:
                    restore_stats[name] = info
            finally:
                os.close(fd)
        linked = {
            name: info for name, info in restore_stats.items() if info.st_nlink == 2
        }
        if linked:
            if set(linked) != restore_names:
                raise _raise_vault_error()
            identities = {(info.st_dev, info.st_ino) for info in linked.values()}
            if len(identities) != 1:
                raise _raise_vault_error()


def _replace_write_through(
    source: Path, destination: Path, *, vault_fd: int | None = None
) -> None:
    if not _IS_WINDOWS:
        if vault_fd is None:
            raise SAKeyVaultError("verified vault handle is required")
        os.replace(
            source.name,
            destination.name,
            src_dir_fd=vault_fd,
            dst_dir_fd=vault_fd,
        )
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # pragma: no cover
    move_file_ex = kernel32.MoveFileExW
    move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    move_file_ex.restype = ctypes.c_int
    if not move_file_ex(str(source), str(destination), 0x1 | 0x8):
        raise OSError(ctypes.get_last_error(), "write-through replacement failed")


def _move_write_through_no_replace(source: Path, destination: Path) -> None:
    if not _IS_WINDOWS:
        raise SAKeyVaultError("Windows write-through move is unavailable")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # pragma: no cover
    move_file_ex = kernel32.MoveFileExW
    move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    move_file_ex.restype = ctypes.c_int
    if not move_file_ex(str(source), str(destination), 0x8):
        raise OSError(ctypes.get_last_error(), "write-through move failed")


def _posix_read_fd(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _windows_read_handle(handle) -> bytes:  # pragma: no cover
    win32file.SetFilePointer(handle, 0, win32file.FILE_BEGIN)
    chunks: list[bytes] = []
    while True:
        _error, chunk = win32file.ReadFile(handle, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _windows_write_handle(handle, body: bytes) -> None:  # pragma: no cover
    offset = 0
    while offset < len(body):
        _error, written = win32file.WriteFile(handle, body[offset:])
        if not isinstance(written, int) or written <= 0:
            raise _raise_vault_error()
        offset += written


def _safe_posix_cleanup_temp(vault_fd: int, name: str, held_fd: int) -> None:
    try:
        _posix_verify_named_identity(vault_fd, name, held_fd)
        os.unlink(name, dir_fd=vault_fd)
        os.fsync(vault_fd)
    except (SAKeyVaultError, FileNotFoundError, OSError):
        return


def _ensure_posix_destination_safe(vault_fd: int, name: str) -> None:
    try:
        fd = _posix_open_child(vault_fd, name)
    except SAKeyVaultError as exc:
        try:
            os.stat(name, dir_fd=vault_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError:
            raise exc
        raise
    else:
        os.close(fd)


def _atomic_write_posix(vault: Path, name: str, body: bytes) -> None:
    temp_name = f".{name}.{uuid4().hex}.tmp"
    with _open_posix_vault_fd("write") as vault_fd:
        _ensure_posix_destination_safe(vault_fd, name)
        temp_fd = _posix_open_child(
            vault_fd, temp_name, writable=True, create_exclusive=True
        )
        published = False
        try:
            os.fchmod(temp_fd, 0o600)
            view = memoryview(body)
            offset = 0
            while offset < len(view):
                written = os.write(temp_fd, view[offset:])
                if written <= 0:
                    raise _raise_vault_error()
                offset += written
            os.fsync(temp_fd)
            _posix_before_publish(temp_name, temp_fd, vault_fd)
            _reject_unsafe_stat(os.fstat(temp_fd), directory=False)
            _posix_verify_named_identity(vault_fd, temp_name, temp_fd)
            _replace_write_through(
                vault / temp_name, vault / name, vault_fd=vault_fd
            )
            published = True
            _posix_verify_named_identity(vault_fd, name, temp_fd)
            info = os.fstat(temp_fd)
            _reject_unsafe_stat(info, directory=False)
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise _raise_vault_error()
            os.fsync(vault_fd)
        except Exception as exc:
            if not published:
                _safe_posix_cleanup_temp(vault_fd, temp_name, temp_fd)
            if isinstance(exc, SAKeyVaultError):
                raise
            raise _raise_vault_error() from exc
        finally:
            os.close(temp_fd)


def _atomic_write_windows(vault: Path, name: str, body: bytes) -> None:  # pragma: no cover
    temp = vault / f".{name}.{uuid4().hex}.tmp"
    destination = vault / name
    with _open_windows_vault("write") as (_vault, _vault_handle, identity):
        if destination.exists():
            _windows_harden_file(destination)
        try:
            with _open_windows_handle(
                temp,
                use=_WindowsHandleUse.TEMP_READ_WRITE,
                directory=False,
                disposition=win32con.CREATE_NEW,
            ) as temp_handle:
                _windows_after_validation(temp, temp_handle, directory=False)
                _set_private_windows_dacl(temp_handle, directory=False)
                _verify_private_windows_dacl(temp_handle, directory=False)
                _windows_write_handle(temp_handle, body)
                win32file.FlushFileBuffers(temp_handle)
                written_sha = hashlib.sha256(
                    _windows_read_handle(temp_handle)
                ).digest()
                if written_sha != hashlib.sha256(body).digest():
                    raise _raise_vault_error()
                _windows_validate_handle(temp_handle, directory=False)
                _windows_recheck_vault_identity(vault, identity)
                _windows_require_named_identity(temp, temp_handle)
                _replace_write_through(temp, destination)
                _windows_recheck_vault_identity(vault, identity)
                _windows_validate_handle(temp_handle, directory=False)
                _windows_require_named_identity(destination, temp_handle)
            _windows_harden_file(destination)
        except Exception as exc:
            try:
                if temp.exists():
                    _remove_windows_verified(vault, temp.name, missing_ok=True)
            except Exception:
                pass
            if isinstance(exc, SAKeyVaultError):
                raise
            raise _raise_vault_error() from exc


def atomic_write(path: Path, body: bytes) -> None:
    vault, name = _assert_direct_child(path)
    if not isinstance(body, bytes):
        raise SAKeyVaultError("SA-key body must be bytes")
    harden_vault()
    if _IS_WINDOWS:  # pragma: no cover
        _atomic_write_windows(vault, name, body)
    else:
        _atomic_write_posix(vault, name, body)


def read_bytes(path: Path) -> bytes:
    vault, name = _assert_direct_child(path)
    harden_vault()
    if _IS_WINDOWS:  # pragma: no cover
        with _open_windows_vault("read"):
            with _open_windows_handle(
                vault / name,
                use=_WindowsHandleUse.READ_OR_HASH,
                directory=False,
                disposition=win32con.OPEN_EXISTING,
            ) as handle:
                return _windows_read_handle(handle)
    with _open_posix_vault_fd("read") as vault_fd:
        fd = _posix_open_child(vault_fd, name)
        try:
            return _posix_read_fd(fd)
        finally:
            os.close(fd)


def file_present(path: Path) -> bool:
    """Return whether a safe regular vault child exists.

    Only a proven absent name returns ``False``.  A symlink, reparse point,
    hardlink, unsafe type, or uncertain lookup raises ``SAKeyVaultError`` so a
    caller cannot mistake hostile residue for a clean vault.
    """
    vault, name = _assert_direct_child(path)
    harden_vault()
    if _IS_WINDOWS:  # pragma: no cover - exercised by mandatory Windows CI
        with _open_windows_vault("presence"):
            try:
                with _open_windows_handle(
                    vault / name,
                    use=_WindowsHandleUse.READ_OR_HASH,
                    directory=False,
                    disposition=win32con.OPEN_EXISTING,
                ):
                    return True
            except _WindowsPathNotFound:
                return False
    with _open_posix_vault_fd("presence") as vault_fd:
        try:
            fd = _posix_open_child(vault_fd, name)
        except SAKeyVaultError as exc:
            try:
                os.stat(name, dir_fd=vault_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            raise exc
        else:
            os.close(fd)
            return True


def _remove_windows_verified(vault: Path, name: str, *, missing_ok: bool) -> None:  # pragma: no cover
    path = vault / name
    with _open_windows_vault("remove") as (_vault, _handle, identity):
        try:
            with _open_windows_handle(
                path,
                use=_WindowsHandleUse.HANDLE_DELETE_OR_RENAME,
                directory=False,
                disposition=win32con.OPEN_EXISTING,
            ) as held_handle:
                _windows_before_path_mutation("remove", path, held_handle)
                _windows_require_named_identity(path, held_handle)
                _windows_recheck_vault_identity(vault, identity)
                win32file.DeleteFile(str(path))
                _windows_recheck_vault_identity(vault, identity)
                _windows_validate_handle(held_handle, directory=False)
        except _WindowsPathNotFound:
            if missing_ok:
                return
            raise
        except SAKeyVaultError:
            raise
        except OSError as exc:
            if missing_ok and getattr(exc, "winerror", None) in {2, 3}:
                return
            raise _raise_vault_error() from exc


def remove(path: Path, *, missing_ok: bool = False) -> None:
    vault, name = _assert_direct_child(path)
    harden_vault()
    if _IS_WINDOWS:  # pragma: no cover
        _remove_windows_verified(vault, name, missing_ok=missing_ok)
        return
    with _open_posix_vault_fd("remove") as vault_fd:
        try:
            fd = _posix_open_child(vault_fd, name)
        except SAKeyVaultError as exc:
            try:
                os.stat(name, dir_fd=vault_fd, follow_symlinks=False)
            except FileNotFoundError:
                if missing_ok:
                    return
            raise exc
        try:
            _posix_verify_named_identity(vault_fd, name, fd)
            os.unlink(name, dir_fd=vault_fd)
            os.fsync(vault_fd)
        except OSError as exc:
            raise _raise_vault_error() from exc
        finally:
            os.close(fd)


def _validate_sha256(value: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise SAKeyVaultError("invalid credential digest")
    return value


def _uuid_from_original_name(name: str) -> UUID:
    match = _UUID_FILE_RE.fullmatch(name)
    if match is None:
        raise SAKeyVaultError("delete quarantine requires a UUID credential")
    key_id = UUID(match.group("uuid"))
    if f"{key_id}.json" != name:
        raise SAKeyVaultError("delete quarantine requires a canonical UUID")
    return key_id


def _ticket_from_name(name: str) -> DeleteQuarantine:
    match = _QUARANTINE_RE.fullmatch(name)
    if match is None:
        raise SAKeyVaultError("unrecognized delete quarantine")
    key_id = UUID(match.group("uuid"))
    sha256 = match.group("sha")
    return DeleteQuarantine(
        key_id=key_id,
        sha256=sha256,
        original_name=f"{key_id}.json",
        quarantine_name=name,
    )


def _validate_ticket(ticket: DeleteQuarantine) -> DeleteQuarantine:
    if not isinstance(ticket, DeleteQuarantine):
        raise SAKeyVaultError("delete quarantine ticket is required")
    parsed = _ticket_from_name(ticket.quarantine_name)
    if parsed != ticket or ticket.original_name != f"{ticket.key_id}.json":
        raise SAKeyVaultError("delete quarantine ticket is inconsistent")
    return ticket


def _hash_posix_child(
    vault_fd: int,
    name: str,
    *,
    allowed_file_links: tuple[int, ...] = (1,),
) -> tuple[str, int]:
    fd = _posix_open_child(
        vault_fd,
        name,
        allowed_file_links=allowed_file_links,
    )
    try:
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest(), fd
    except Exception:
        os.close(fd)
        raise


def _hash_windows_path(path: Path) -> str:  # pragma: no cover
    with _open_windows_handle(
        path,
        use=_WindowsHandleUse.READ_OR_HASH,
        directory=False,
        disposition=win32con.OPEN_EXISTING,
    ) as handle:
        return _windows_hash_handle(handle)


def quarantine_for_delete(path: Path, *, expected_sha256: str) -> DeleteQuarantine:
    vault, name = _assert_direct_child(path)
    key_id = _uuid_from_original_name(name)
    expected = _validate_sha256(expected_sha256)
    harden_vault()
    quarantine_name = f".{key_id}.json.{expected}.{uuid4().hex}.delete-quarantine"
    ticket = DeleteQuarantine(key_id, expected, name, quarantine_name)
    if _IS_WINDOWS:  # pragma: no cover
        with _open_windows_vault("quarantine") as (_vault, _handle, identity):
            source = vault / name
            destination = vault / quarantine_name
            if destination.exists():
                raise _raise_vault_error()
            with _open_windows_handle(
                source,
                use=_WindowsHandleUse.READ_OR_HASH,
                directory=False,
                disposition=win32con.OPEN_EXISTING,
            ) as source_handle:
                if _windows_hash_handle(source_handle) != expected:
                    raise _raise_vault_error()
                _windows_before_path_mutation(
                    "quarantine", source, source_handle
                )
                _windows_require_named_identity(source, source_handle)
                _windows_recheck_vault_identity(vault, identity)
                _move_write_through_no_replace(source, destination)
                _windows_recheck_vault_identity(vault, identity)
                _windows_validate_handle(source_handle, directory=False)
                if _windows_hash_handle(source_handle) != expected:
                    raise _raise_vault_error()
                with _open_windows_handle(
                    destination,
                    use=_WindowsHandleUse.READ_OR_HASH,
                    directory=False,
                    disposition=win32con.OPEN_EXISTING,
                ) as destination_handle:
                    if _windows_identity(destination_handle) != _windows_identity(
                        source_handle
                    ):
                        raise _raise_vault_error()
                    if _windows_hash_handle(destination_handle) != expected:
                        raise _raise_vault_error()
        return ticket
    with _open_posix_vault_fd("quarantine") as vault_fd:
        digest, source_fd = _hash_posix_child(vault_fd, name)
        try:
            if digest != expected:
                raise _raise_vault_error()
            _posix_verify_named_identity(vault_fd, name, source_fd)
            try:
                os.stat(quarantine_name, dir_fd=vault_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise _raise_vault_error()
            os.replace(
                name,
                quarantine_name,
                src_dir_fd=vault_fd,
                dst_dir_fd=vault_fd,
            )
            _posix_verify_named_identity(vault_fd, quarantine_name, source_fd)
            os.fsync(vault_fd)
        except OSError as exc:
            raise _raise_vault_error() from exc
        finally:
            os.close(source_fd)
    return ticket


def _finish_quarantine_posix_on_fd(
    ticket: DeleteQuarantine, *, restore: bool, vault_fd: int
) -> None:
    allowed_links = (1, 2)
    digest, quarantine_fd = _hash_posix_child(
        vault_fd,
        ticket.quarantine_name,
        allowed_file_links=allowed_links,
    )
    try:
        if digest != ticket.sha256:
            raise _raise_vault_error()
        quarantine_info = os.fstat(quarantine_fd)
        if stat.S_IMODE(quarantine_info.st_mode) != 0o600:
            raise _raise_vault_error()
        _posix_verify_named_identity(
            vault_fd,
            ticket.quarantine_name,
            quarantine_fd,
            allowed_file_links=allowed_links,
        )
        if restore:
            published_from_quarantine = False
            try:
                canonical_fd = _posix_open_child(
                    vault_fd,
                    ticket.original_name,
                    allowed_file_links=(1, 2),
                )
            except SAKeyVaultError as exc:
                try:
                    os.stat(
                        ticket.original_name,
                        dir_fd=vault_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    if os.fstat(quarantine_fd).st_nlink != 1:
                        raise _raise_vault_error()
                    os.link(
                        ticket.quarantine_name,
                        ticket.original_name,
                        src_dir_fd=vault_fd,
                        dst_dir_fd=vault_fd,
                        follow_symlinks=False,
                    )
                    published_from_quarantine = True
                    canonical_fd = _posix_open_child(
                        vault_fd,
                        ticket.original_name,
                        allowed_file_links=(2,),
                    )
                else:
                    raise exc
            try:
                quarantine_info = os.fstat(quarantine_fd)
                canonical_info = os.fstat(canonical_fd)
                same_identity = (
                    quarantine_info.st_dev,
                    quarantine_info.st_ino,
                ) == (canonical_info.st_dev, canonical_info.st_ino)
                if same_identity:
                    if (
                        quarantine_info.st_nlink != 2
                        or canonical_info.st_nlink != 2
                    ):
                        raise _raise_vault_error()
                    _posix_verify_named_identity(
                        vault_fd,
                        ticket.original_name,
                        canonical_fd,
                        allowed_file_links=(2,),
                    )
                    _posix_verify_named_identity(
                        vault_fd,
                        ticket.quarantine_name,
                        quarantine_fd,
                        allowed_file_links=(2,),
                    )
                else:
                    if published_from_quarantine:
                        raise _raise_vault_error()
                    _reject_unsafe_stat(canonical_info, directory=False)
                    _posix_before_duplicate_restore_delete(
                        ticket.original_name,
                        canonical_fd,
                        ticket.quarantine_name,
                        quarantine_fd,
                        vault_fd,
                    )
                    _posix_verify_named_identity(
                        vault_fd, ticket.original_name, canonical_fd
                    )
                    _posix_verify_named_identity(
                        vault_fd, ticket.quarantine_name, quarantine_fd
                    )
                os.lseek(canonical_fd, 0, os.SEEK_SET)
                canonical_hash = hashlib.sha256(
                    _posix_read_fd(canonical_fd)
                ).hexdigest()
                if (
                    canonical_hash != ticket.sha256
                    or stat.S_IMODE(os.fstat(canonical_fd).st_mode) != 0o600
                ):
                    raise _raise_vault_error()
                os.fsync(canonical_fd)
                os.fsync(vault_fd)
                os.unlink(ticket.quarantine_name, dir_fd=vault_fd)
                _posix_verify_named_identity(
                    vault_fd, ticket.original_name, canonical_fd
                )
                os.lseek(canonical_fd, 0, os.SEEK_SET)
                if (
                    hashlib.sha256(_posix_read_fd(canonical_fd)).hexdigest()
                    != ticket.sha256
                    or stat.S_IMODE(os.fstat(canonical_fd).st_mode) != 0o600
                ):
                    raise _raise_vault_error()
                os.fsync(canonical_fd)
            finally:
                os.close(canonical_fd)
        else:
            if quarantine_info.st_nlink == 2:
                if not _posix_validate_crashed_restore_pair(vault_fd, ticket):
                    raise _raise_vault_error()
                canonical_fd = _posix_open_child(
                    vault_fd,
                    ticket.original_name,
                    allowed_file_links=(2,),
                )
                try:
                    canonical_info = os.fstat(canonical_fd)
                    if (
                        canonical_info.st_dev,
                        canonical_info.st_ino,
                    ) != (quarantine_info.st_dev, quarantine_info.st_ino):
                        raise _raise_vault_error()
                    _posix_verify_named_identity(
                        vault_fd,
                        ticket.original_name,
                        canonical_fd,
                        allowed_file_links=(2,),
                    )
                    _posix_verify_named_identity(
                        vault_fd,
                        ticket.quarantine_name,
                        quarantine_fd,
                        allowed_file_links=(2,),
                    )
                    # DB absence is authoritative. Remove the published name first,
                    # so a crash can leave only a quarantine for the next reconcile.
                    os.unlink(ticket.original_name, dir_fd=vault_fd)
                    os.fsync(vault_fd)
                    _posix_verify_named_identity(
                        vault_fd,
                        ticket.quarantine_name,
                        quarantine_fd,
                    )
                finally:
                    os.close(canonical_fd)
            os.unlink(ticket.quarantine_name, dir_fd=vault_fd)
        os.fsync(vault_fd)
    except OSError as exc:
        raise _raise_vault_error() from exc
    finally:
        os.close(quarantine_fd)


def _finish_quarantine_posix(ticket: DeleteQuarantine, *, restore: bool) -> None:
    operation = "restore" if restore else "discard"
    with _open_posix_vault_fd(operation) as vault_fd:
        _finish_quarantine_posix_on_fd(
            ticket, restore=restore, vault_fd=vault_fd
        )


def _finish_quarantine_windows(ticket: DeleteQuarantine, *, restore: bool) -> None:  # pragma: no cover
    vault = storage.sa_key_dir()
    quarantine = vault / ticket.quarantine_name
    canonical = vault / ticket.original_name
    operation = "restore" if restore else "discard"
    with _open_windows_vault(operation) as (
        _vault,
        _handle,
        identity,
    ):
        with _open_windows_handle(
            quarantine,
            use=_WindowsHandleUse.READ_OR_HASH,
            directory=False,
            disposition=win32con.OPEN_EXISTING,
        ) as quarantine_handle:
            if _windows_hash_handle(quarantine_handle) != ticket.sha256:
                raise _raise_vault_error()
            if restore:
                with contextlib.ExitStack() as canonical_stack:
                    try:
                        canonical_handle = canonical_stack.enter_context(
                            _open_windows_handle(
                                canonical,
                                use=_WindowsHandleUse.READ_OR_HASH,
                                directory=False,
                                disposition=win32con.OPEN_EXISTING,
                            )
                        )
                    except _WindowsPathNotFound:
                        canonical_handle = None
                    if canonical_handle is not None:
                        if _windows_hash_handle(canonical_handle) != ticket.sha256:
                            raise _raise_vault_error()
                        _windows_before_path_mutation(
                            "restore-discard-duplicate",
                            quarantine,
                            quarantine_handle,
                        )
                        _windows_require_named_identity(
                            canonical, canonical_handle
                        )
                        if _windows_hash_handle(canonical_handle) != ticket.sha256:
                            raise _raise_vault_error()
                        _windows_require_named_identity(
                            quarantine, quarantine_handle
                        )
                        _windows_recheck_vault_identity(vault, identity)
                        win32file.DeleteFile(str(quarantine))
                        _windows_recheck_vault_identity(vault, identity)
                        _windows_validate_handle(
                            quarantine_handle, directory=False
                        )
                        _windows_validate_handle(canonical_handle, directory=False)
                        _windows_require_named_identity(
                            canonical, canonical_handle
                        )
                        if _windows_hash_handle(canonical_handle) != ticket.sha256:
                            raise _raise_vault_error()
                        return
                _windows_before_path_mutation(
                    "restore", quarantine, quarantine_handle
                )
                _windows_require_named_identity(quarantine, quarantine_handle)
                _windows_recheck_vault_identity(vault, identity)
                _move_write_through_no_replace(quarantine, canonical)
                _windows_recheck_vault_identity(vault, identity)
                _windows_validate_handle(quarantine_handle, directory=False)
                if _windows_hash_handle(quarantine_handle) != ticket.sha256:
                    raise _raise_vault_error()
                with _open_windows_handle(
                    canonical,
                    use=_WindowsHandleUse.READ_OR_HASH,
                    directory=False,
                    disposition=win32con.OPEN_EXISTING,
                ) as canonical_handle:
                    if _windows_identity(canonical_handle) != _windows_identity(
                        quarantine_handle
                    ):
                        raise _raise_vault_error()
                    if _windows_hash_handle(canonical_handle) != ticket.sha256:
                        raise _raise_vault_error()
            else:
                _windows_before_path_mutation(
                    "discard", quarantine, quarantine_handle
                )
                _windows_require_named_identity(quarantine, quarantine_handle)
                _windows_recheck_vault_identity(vault, identity)
                win32file.DeleteFile(str(quarantine))
                _windows_recheck_vault_identity(vault, identity)
                _windows_validate_handle(quarantine_handle, directory=False)


def restore_quarantined_delete(ticket: DeleteQuarantine) -> None:
    ticket = _validate_ticket(ticket)
    if _IS_WINDOWS:  # pragma: no cover
        harden_vault()
        _finish_quarantine_windows(ticket, restore=True)
    else:
        harden_vault(_restore_ticket=ticket)
        _finish_quarantine_posix(ticket, restore=True)


def discard_quarantined_delete(ticket: DeleteQuarantine) -> None:
    ticket = _validate_ticket(ticket)
    harden_vault()
    if _IS_WINDOWS:  # pragma: no cover
        _finish_quarantine_windows(ticket, restore=False)
    else:
        _finish_quarantine_posix(ticket, restore=False)


def _normalize_expected(expected_sha256: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_sha in expected_sha256.items():
        key = str(raw_key)
        if key.endswith(".json"):
            key = key[:-5]
        try:
            key_id = UUID(key)
        except (ValueError, TypeError, AttributeError) as exc:
            raise SAKeyVaultError("invalid credential inventory key") from exc
        canonical = str(key_id)
        if key != canonical or canonical in normalized:
            raise SAKeyVaultError("invalid credential inventory key")
        normalized[canonical] = _validate_sha256(raw_sha)
    return normalized


def _vault_names() -> list[str]:
    harden_vault()
    if _IS_WINDOWS:  # pragma: no cover
        with _open_windows_vault("inventory") as (vault, _handle, identity):
            _windows_recheck_vault_identity(vault, identity)
            names = [path.name for path in vault.iterdir()]
            _windows_recheck_vault_identity(vault, identity)
            return names
    with _open_posix_vault_fd("inventory") as vault_fd:
        return list(os.listdir(vault_fd))


def reconcile_delete_quarantines(expected_sha256: Mapping[str, str]) -> None:
    expected = _normalize_expected(expected_sha256)
    harden_vault()
    if not _IS_WINDOWS:
        with _open_posix_vault_fd("reconcile") as vault_fd:
            grouped = _group_quarantines(os.listdir(vault_fd))
            for key_id, ticket in grouped.items():
                db_sha = expected.get(str(key_id))
                if db_sha is None:
                    _finish_quarantine_posix_on_fd(
                        ticket, restore=False, vault_fd=vault_fd
                    )
                elif db_sha == ticket.sha256:
                    _finish_quarantine_posix_on_fd(
                        ticket, restore=True, vault_fd=vault_fd
                    )
                else:
                    raise _raise_vault_error()
        return
    grouped = _group_quarantines(_vault_names())  # pragma: no cover - Windows CI
    for key_id, ticket in grouped.items():  # pragma: no cover - Windows CI
        db_sha = expected.get(str(key_id))
        if db_sha is None:
            discard_quarantined_delete(ticket)
        elif db_sha == ticket.sha256:
            restore_quarantined_delete(ticket)
        else:
            raise _raise_vault_error()


def _group_quarantines(names: list[str]) -> dict[UUID, DeleteQuarantine]:
    grouped: dict[UUID, list[DeleteQuarantine]] = {}
    for name in names:
        if not name.endswith(".delete-quarantine"):
            continue
        ticket = _ticket_from_name(name)
        grouped.setdefault(ticket.key_id, []).append(ticket)
    if any(len(tickets) != 1 for tickets in grouped.values()):
        raise _raise_vault_error()
    return {key_id: tickets[0] for key_id, tickets in grouped.items()}


def snapshot_uuid_inventory() -> dict[str, str]:
    """Return a verified UUID-key digest snapshot without exposing bytes.

    This is the read-only operational evidence surface. It uses the same held
    directory-fd / Windows-handle path as startup inventory verification, and
    rejects every unresolved quarantine or unrecognized/unsafe vault entry.
    """

    harden_vault()
    if not _IS_WINDOWS:
        observed: dict[str, str] = {}
        with _open_posix_vault_fd("inventory") as vault_fd:
            for name in os.listdir(vault_fd):
                key_id = _inventory_key_id(name)
                if key_id is None:
                    continue
                digest, fd = _hash_posix_child(vault_fd, name)
                os.close(fd)
                observed[key_id] = digest
        return observed
    observed: dict[str, str] = {}
    with _open_windows_vault("inventory") as (  # pragma: no cover - Windows CI
        vault,
        vault_handle,
        identity,
    ):
        _windows_recheck_vault_identity(vault, identity)
        try:
            names = [path.name for path in vault.iterdir()]
        except OSError as exc:
            raise _raise_vault_error() from exc
        _windows_recheck_vault_identity(vault, identity)
        for name in names:
            key_id = _inventory_key_id(name)
            if key_id is None:
                continue
            _windows_before_inventory_child(vault, name, vault_handle)
            _windows_validate_handle(vault_handle, directory=True)
            _windows_recheck_vault_identity(vault, identity)
            child = vault / name
            with _open_windows_handle(
                child,
                use=_WindowsHandleUse.READ_OR_HASH,
                directory=False,
                disposition=win32con.OPEN_EXISTING,
            ) as child_handle:
                _windows_require_named_identity(child, child_handle)
                _windows_recheck_vault_identity(vault, identity)
                digest = _windows_hash_handle(child_handle)
                _windows_validate_handle(child_handle, directory=False)
                _windows_require_named_identity(child, child_handle)
                _windows_recheck_vault_identity(vault, identity)
            observed[key_id] = digest
    return observed


def verify_uuid_inventory(expected_sha256: Mapping[str, str]) -> None:
    expected = _normalize_expected(expected_sha256)
    observed = snapshot_uuid_inventory()
    if observed != expected:
        raise _raise_vault_error()


def _inventory_key_id(name: str) -> str | None:
    if name.endswith(".delete-quarantine"):
        raise _raise_vault_error()
    if name == "active.json" or _WRITE_TEMP_RE.fullmatch(name):
        return None
    match = _UUID_FILE_RE.fullmatch(name)
    if match is None:
        raise _raise_vault_error()
    return match.group("uuid")
