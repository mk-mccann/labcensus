"""Core records shared across the tool.

Detectors and heuristics consume only these, never a filesystem object, so a
future S3 or SSH backend is an addition rather than a rewrite.

``path`` is a :class:`pathlib.PurePath` whose flavour is chosen by the backend
that produced it — ``PureWindowsPath`` on Windows, ``PurePosixPath`` on POSIX.
Pinning it at the point of origin is what makes Windows drive paths, UNC paths
and POSIX names containing a backslash all parse correctly. ``str(path)``
returns the native form.

Fields a backend cannot supply are ``| None`` rather than a stand-in value.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import cache
from pathlib import PurePath


class OwnerKind(str, Enum):
    POSIX = "posix"
    POSIX_GROUP = "posix_group"
    WINDOWS = "windows"


@dataclass(frozen=True, slots=True)
class Owner:
    """A file's owning principal: a POSIX uid or gid, or a Windows SID.

    Kept raw and unresolved. Construct via :func:`posix_owner`,
    :func:`posix_group` or :func:`windows_owner`, which intern instances.
    """

    kind: OwnerKind
    id: str


@cache
def posix_owner(uid: int) -> Owner:
    return Owner(OwnerKind.POSIX, str(uid))


@cache
def posix_group(gid: int) -> Owner:
    """A POSIX group, kept distinct from a uid of the same number."""
    return Owner(OwnerKind.POSIX_GROUP, str(gid))


@cache
def windows_owner(sid: str) -> Owner:
    return Owner(OwnerKind.WINDOWS, sid)


@dataclass(frozen=True, slots=True)
class FileStat:
    """A single regular file, as seen by a backend.

    Every field comes from one ``stat`` call, so none of it costs extra I/O.

    ``size`` is apparent size and ``blocks`` is what the filesystem actually
    allocated; they diverge under sparse files, compression and deduplication.
    ``btime`` is true creation time where the platform provides one, and
    ``None`` where it does not. ``atime`` is unreliable on filesystems mounted
    ``relatime`` or ``noatime``.

    Symlinks are recorded but never followed; ``link_target`` is the unresolved
    target. ``name_raw`` holds the original bytes when a filename was not valid
    UTF-8, in which case ``path`` carries a decodable substitute.
    """

    path: PurePath
    size: int
    blocks: int | None
    mtime: float
    btime: float | None
    atime: float | None
    owner: Owner | None
    group: Owner | None
    mode: int | None
    ino: int | None
    dev: int | None
    nlink: int | None
    islink: bool
    link_target: str | None = None
    name_raw: bytes | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def suffix(self) -> str:
        """Lowercased extension including the dot, or "" if there is none."""
        return self.path.suffix.lower()

    @property
    def hardlink_key(self) -> tuple[int, int] | None:
        """Identity for deduplicating hardlinks, or ``None`` if unavailable.

        ``None`` means the caller should count the file rather than guess.
        """
        if self.nlink is None or self.nlink <= 1:
            return None
        if self.ino is None or self.dev is None:
            return None
        return (self.dev, self.ino)


@dataclass(frozen=True, slots=True)
class DirListing:
    """One directory's immediate contents, as the walker yields it.

    Membership sets are precomputed once, since every detector interrogates the
    same listing.
    """

    path: PurePath
    files: tuple[FileStat, ...]
    subdirs: frozenset[str]
    filenames: frozenset[str]
    _subdirs_folded: frozenset[str]
    _filenames_folded: frozenset[str]

    @classmethod
    def build(
        cls,
        path: PurePath,
        files: tuple[FileStat, ...],
        subdirs: frozenset[str] | set[str],
    ) -> DirListing:

        filenames = frozenset(f.name for f in files)
        subdirs = frozenset(subdirs)

        return cls(
            path=path,
            files=files,
            subdirs=subdirs,
            filenames=filenames,
            _subdirs_folded=frozenset(s.casefold() for s in subdirs),
            _filenames_folded=frozenset(f.casefold() for f in filenames),
        )

    @property
    def name(self) -> str:
        return self.path.name

    def has_file(self, name: str) -> bool:
        """Whether the directory holds this file, ignoring case.

        NTFS and the macOS default are case-insensitive, so ``Settings.xml``
        and ``settings.xml`` are the same finding.
        """

        return name.casefold() in self._filenames_folded

    def has_subdir(self, name: str) -> bool:
        return name.casefold() in self._subdirs_folded


@dataclass(frozen=True, slots=True)
class WalkError:
    """A path the walker could not read. Reported, never raised."""

    path: PurePath
    reason: str
