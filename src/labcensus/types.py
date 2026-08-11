"""Core data types forming the backend seam.

Detectors and heuristics consume only these records. They never import ``os``
or touch a filesystem object directly, which is what allows a future S3 or SSH
backend to be an addition rather than a rewrite.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FileStat:
    """A single regular file, as seen by a backend.

    Constructed once per file on the scanned tree, so this is a plain frozen
    dataclass rather than a pydantic model. Pydantic appears only at the
    report boundary, where it is called once.

    Symlinks are recorded but never followed. ``uid`` is kept raw: resolving it
    to an account name is a cached, separate concern, and whether it resolves
    at all is itself evidence the orphan heuristic needs.
    """

    path: str
    size: int
    mtime: float
    uid: int
    gid: int
    ino: int
    dev: int
    nlink: int
    islink: bool

    @property
    def name(self) -> str:
        return posixpath.basename(self.path)

    @property
    def suffix(self) -> str:
        """Lowercased extension including the dot, or "" if there is none.

        Lowercased because ``.TIF`` and ``.tif`` are one format, and mixed case
        is common in microscopy exports.
        """
        return posixpath.splitext(self.path)[1].lower()


@dataclass(frozen=True, slots=True)
class DirListing:
    """One directory's immediate contents.

    The unit the walker yields. Directory structure is the strongest modality
    signal, so detectors sniff this before they look at any individual file.
    Membership sets are precomputed once per directory rather than per query,
    since every registered detector interrogates the same listing.
    """

    path: str
    files: tuple[FileStat, ...]
    subdirs: frozenset[str]
    filenames: frozenset[str]

    @classmethod
    def build(
        cls,
        path: str,
        files: tuple[FileStat, ...],
        subdirs: frozenset[str] | set[str],
    ) -> DirListing:
        return cls(
            path=path,
            files=files,
            subdirs=frozenset(subdirs),
            filenames=frozenset(f.name for f in files),
        )

    @property
    def name(self) -> str:
        return posixpath.basename(self.path)


@dataclass(frozen=True, slots=True)
class WalkError:
    """A path the walker could not read.

    Unreadable directories are census findings, not failures: a tree nobody can
    read is exactly the kind of thing a PI needs told. These are collected and
    reported, never raised.
    """

    path: str
    reason: str
