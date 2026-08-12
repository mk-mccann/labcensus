"""Core data types forming the backend seam.

Detectors and heuristics consume only these records. They never import ``os``
or touch a filesystem object directly, which is what allows a future S3 or SSH
backend to be an addition rather than a rewrite.

Fields a backend may be unable to supply are typed ``| None`` rather than
defaulted to a sentinel. POSIX and Windows expose genuinely different metadata,
and a heuristic that silently treats "unknown" as a real value is how the
orphan score produces confident nonsense.

Paths
-----

``path`` is a :class:`pathlib.PurePath`, and **the backend chooses its flavour**:
``PureWindowsPath`` on Windows, ``PurePosixPath`` on POSIX. Nothing else in the
system decides, because nothing else knows.

That distinction is the whole point. String splitting cannot be written
portably — ``posixpath`` does not split on ``\\``, ``ntpath`` mangles POSIX
filenames that legally contain one — and ``pathlib.Path`` is no better, since a
concrete ``Path`` binds to whichever platform is *running* rather than whichever
platform produced the path. Pinning the flavour at the point of origin is what
makes every case correct at once: ``C:\\lab\\rec\\settings.xml`` and
``\\\\server\\share\\rec`` parse as Windows, while a POSIX file genuinely named
``weird\\name.tif`` keeps its backslash.

Constructing a ``PurePath`` per file is not free — roughly 4 s per million files
— but a scan of that size runs for minutes against a NAS, and the cost buys
correctness on the target platform plus ``.name``, ``.suffix``, ``.parent``,
``.parts`` and ``.with_suffix()`` for detectors, which need all of them:
SpikeGLX is a same-stem ``.bin``/``.meta`` pairing, and ALF is a path-shape
match. ``str(path)`` returns the native form, so the report still shows a PI a
path they can paste into their own file browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import cache
from pathlib import PurePath


class OwnerKind(str, Enum):
    POSIX = "posix"
    WINDOWS = "windows"


@dataclass(frozen=True, slots=True)
class Owner:
    """A file's owning principal, as the platform expresses it.

    POSIX gives a numeric uid; Windows gives a SID. Both are kept raw and
    unresolved: whether an identifier still maps to a live account is the
    orphan heuristic's strongest evidence, so resolution belongs to a cached
    resolver at report time, not to the hot path.

    Construct via :func:`posix_owner` / :func:`windows_owner`, which intern
    instances — a tree has millions of files but a handful of owners.
    """

    kind: OwnerKind
    id: str


@cache
def posix_owner(uid: int) -> Owner:
    return Owner(OwnerKind.POSIX, str(uid))


@cache
def windows_owner(sid: str) -> Owner:
    return Owner(OwnerKind.WINDOWS, sid)


@dataclass(frozen=True, slots=True)
class FileStat:
    """A single regular file, as seen by a backend.

    Constructed once per file on the scanned tree, so this is a plain frozen
    dataclass rather than a pydantic model. Pydantic appears only at the
    report boundary, where it is called once.

    ``ino``/``dev``/``nlink`` are optional because they are not free
    everywhere: on Windows ``DirEntry.inode()`` costs a system call that Unix
    does not charge, which over an SMB mount is a network round trip per file.
    A backend that cannot supply them cheaply, or at all, passes ``None``.

    Symlinks are recorded but never followed. A backend maps whatever its
    platform calls a link — including Windows junctions and reparse points —
    onto ``islink``.
    """

    path: PurePath
    size: int
    mtime: float
    owner: Owner | None
    ino: int | None
    dev: int | None
    nlink: int | None
    islink: bool

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def suffix(self) -> str:
        """Lowercased extension including the dot, or "" if there is none.

        Lowercased because ``.TIF`` and ``.tif`` are one format, and mixed case
        is common in microscopy exports.
        """
        return self.path.suffix.lower()

    @property
    def hardlink_key(self) -> tuple[int, int] | None:
        """Identity for size deduplication, or ``None`` if not deduplicable.

        Counting a hardlinked file once per name inflates the report's headline
        volume number. Returns ``None`` when the file has no second link, or
        when the backend could not supply the identifying fields — in which
        case the caller must count the file rather than guess.
        """
        if self.nlink is None or self.nlink <= 1:
            return None
        if self.ino is None or self.dev is None:
            return None
        return (self.dev, self.ino)


@dataclass(frozen=True, slots=True)
class DirListing:
    """One directory's immediate contents.

    The unit the walker yields. Directory structure is the strongest modality
    signal, so detectors sniff this before they look at any individual file.
    Membership sets are precomputed once per directory rather than per query,
    since every registered detector interrogates the same listing.
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
        """Case-insensitive membership test, which is what detectors want.

        NTFS and the macOS default APFS configuration are both case-insensitive,
        so a rig that writes ``Settings.xml`` and one that writes
        ``settings.xml`` are the same finding.
        """

        return name.casefold() in self._filenames_folded

    def has_subdir(self, name: str) -> bool:
        return name.casefold() in self._subdirs_folded


@dataclass(frozen=True, slots=True)
class WalkError:
    """A path the walker could not read.

    Unreadable directories are census findings, not failures: a tree nobody can
    read is exactly the kind of thing a PI needs told. These are collected and
    reported, never raised.
    """

    path: PurePath
    reason: str
