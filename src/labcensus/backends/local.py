"""The local filesystem, via ``os.scandir``."""

from __future__ import annotations

import os
import sys
from pathlib import PurePath, PureWindowsPath
from typing import TYPE_CHECKING

from ..types import (
    DirListing,
    FileStat,
    Owner,
    WalkError,
    posix_group,
    posix_owner,
    windows_owner,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

IS_WINDOWS = sys.platform == "win32"

#: Inode lookups cost a system call on Windows that Unix does not charge, and
#: buy only hardlink deduplication. Not worth it there.
PAY_FOR_INODE = not IS_WINDOWS


def _decode(name: str) -> tuple[str, bool, bytes | None]:
    """Return a storable name, whether it was lossy, and the raw bytes if so.

    POSIX filenames are bytes and need not be valid text. Undecodable bytes are
    replaced so the name can be stored and displayed, and the original is kept
    alongside so nothing is lost.
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
        """A path in this platform's flavour."""
        return PureWindowsPath(path) if IS_WINDOWS else PurePath(path)

    def list_dir(
        self, path: str | os.PathLike[str]
    ) -> tuple[DirListing | None, list[WalkError]]:
        """One directory's immediate contents, plus anything unreadable in it.

        Errors are returned rather than raised, so a scan completes on storage
        it cannot fully read.
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
        """Child directories of a listing, sorted for a repeatable scan."""
        for name in sorted(listing.subdirs):
            yield listing.path / name

    def peek(self, path: str | os.PathLike[str], nbytes: int) -> bytes:
        """The first ``nbytes`` of a file. The only call here that opens one."""
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

    def _owner(self, st: os.stat_result) -> Owner | None:
        if not IS_WINDOWS:
            return posix_owner(st.st_uid)
        sid = _windows_sid(st)
        return windows_owner(sid) if sid is not None else None


def _windows_sid(st: os.stat_result) -> str | None:
    """The owning SID, or ``None`` until SID resolution is implemented.

    ``st_uid`` is always ``0`` on Windows, so reporting ownership as unavailable
    is the only honest answer available here.
    """
    return None


def _reason(exc: BaseException) -> str:
    """Why a path could not be read."""
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return type(exc).__name__
