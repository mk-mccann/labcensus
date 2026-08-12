"""The local filesystem, via ``os.scandir``.

Deliberately not fsspec. Its ``LocalFileSystem.info()`` copies ``uid``, ``ino``
and ``nlink`` off ``os.stat_result`` with no guard, so on Windows — where
``st_uid`` is always ``0`` — it returns a confident ``uid: 0`` for every file on
the volume. That is exactly the uniform lie the :class:`~labcensus.types.Owner`
model exists to prevent. Its ``created`` field silently becomes ``st_ctime`` on
Linux, and ``make_path_posix`` imposes a path normalisation we have not chosen.
fsspec returns when there is an S3 or SSH backend to write, where it earns its
keep.

``os.scandir`` also hands back a ``DirEntry`` whose ``stat()`` is already cached
from the directory read on Unix, so the metadata below costs no extra system
call.
"""

from __future__ import annotations

import os
import stat as stat_module
import sys
from pathlib import PurePath, PureWindowsPath
from typing import TYPE_CHECKING

from ..types import (
    DirListing,
    FileStat,
    WalkError,
    posix_group,
    posix_owner,
    windows_owner,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

IS_WINDOWS = sys.platform == "win32"

#: Windows charges a system call for ``DirEntry.inode()`` that Unix does not,
#: which over an SMB mount is a network round trip per file. The field buys only
#: hardlink deduplication, which is near-pointless on NTFS. Decline to pay.
PAY_FOR_INODE = not IS_WINDOWS


def _decode(name: str) -> tuple[str, bool, bytes | None]:
    """Return a storable name, whether it was lossy, and the raw bytes if so.

    POSIX filenames are bytes, not text. ``os.scandir`` surfaces undecodable
    ones with surrogate escapes, which SQLite's TEXT type cannot store and
    ``json`` cannot encode. Rather than fail a multi-hour walk on one badly
    named file, replace the undecodable bytes and keep the original alongside,
    so nothing is lost and the report can say plainly that a name was not valid
    text.
    """
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        raw = name.encode("utf-8", "surrogateescape")
        return raw.decode("utf-8", "replace"), True, raw
    return name, False, None


class LocalBackend:
    """Reads one directory at a time. Never recurses, never follows a link."""

    def __init__(self, *, pay_for_inode: bool = PAY_FOR_INODE) -> None:
        self._pay_for_inode = pay_for_inode

    def to_pure(self, path: str | os.PathLike[str]) -> PurePath:
        """A path in this platform's flavour.

        The flavour is pinned here, at the point of origin, because this is the
        only layer that knows it. A concrete ``Path`` would bind to whichever
        platform is *running*, which is the same bug in nicer packaging.
        """
        return PureWindowsPath(path) if IS_WINDOWS else PurePath(path)

    def list_dir(
        self, path: str | os.PathLike[str]
    ) -> tuple[DirListing | None, list[WalkError]]:
        """One directory's immediate contents, plus anything unreadable in it.

        Errors are returned, never raised. A tree nobody can read is a census
        finding — arguably the most useful one — and a NAS produces permission
        errors within seconds of starting.
        """
        base = self.to_pure(path)
        errors: list[WalkError] = []
        files: list[FileStat] = []
        subdirs: set[str] = set()

        try:
            entries = list(os.scandir(path))
        except (OSError, ValueError) as exc:
            errors.append(WalkError(path=base, reason=_reason(exc)))
            return None, errors

        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    subdirs.add(entry.name)
                    continue
                files.append(self._to_filestat(entry, base))
            except (OSError, ValueError) as exc:
                errors.append(WalkError(path=base / entry.name, reason=_reason(exc)))

        return DirListing.build(base, tuple(files), subdirs), errors

    def subdir_paths(self, listing: DirListing) -> Iterator[PurePath]:
        """Child directories of a listing, in a stable order.

        Sorted so that a scan of the same tree twice produces the same index.
        Filesystem order is arbitrary and would otherwise leak into the output.
        """
        for name in sorted(listing.subdirs):
            yield listing.path / name

    def peek(self, path: str | os.PathLike[str], nbytes: int) -> bytes:
        """The first ``nbytes`` of a file.

        The only method here that opens anything, which is what gives the
        sampling policy exactly one thing to gate. No detector calls it yet.
        """
        with open(path, "rb") as handle:
            return handle.read(nbytes)

    def _to_filestat(self, entry: os.DirEntry[str], base: PurePath) -> FileStat:
        st = entry.stat(follow_symlinks=False)
        islink = entry.is_symlink()

        name, _lossy, raw = _decode(entry.name)
        path = base / name

        link_target = None
        if islink:
            try:
                link_target = os.readlink(entry.path)
            except OSError:
                link_target = None

        return FileStat(
            path=path,
            size=st.st_size,
            blocks=getattr(st, "st_blocks", None),
            mtime=st.st_mtime,
            btime=getattr(st, "st_birthtime", None),
            atime=st.st_atime,
            owner=self._owner(st),
            group=None if IS_WINDOWS else posix_group(st.st_gid),
            mode=st.st_mode,
            ino=entry.inode() if self._pay_for_inode else None,
            dev=st.st_dev if self._pay_for_inode else None,
            nlink=st.st_nlink if self._pay_for_inode else None,
            islink=islink,
            link_target=link_target,
            name_raw=raw,
        )

    def _owner(self, st: os.stat_result) -> object | None:
        if not IS_WINDOWS:
            return posix_owner(st.st_uid)
        sid = _windows_sid(st)
        return windows_owner(sid) if sid is not None else None


def _windows_sid(st: os.stat_result) -> str | None:
    """Placeholder for SID resolution.

    ``st_uid`` is always ``0`` on Windows, so returning it would feed the orphan
    heuristic a uniform, confident lie across an entire platform. Until this
    reads the real SID via ``win32security.GetFileSecurity``, it reports that
    ownership is unavailable, which the heuristic already knows how to handle.
    """
    return None


def _reason(exc: BaseException) -> str:
    """A short, stable description of why a path could not be read."""
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return type(exc).__name__


def _is_dir_mode(mode: int) -> bool:
    return stat_module.S_ISDIR(mode)
