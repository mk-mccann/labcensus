"""Reconstruct ``DirListing``s from a finished index.

The seam that lets classification run against a scan without touching the
filesystem again: detectors take a ``DirListing`` and nothing else, so this
is what keeps them unmodified whether they run live or from an index.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from itertools import groupby
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING

from ..types import (
    DirListing,
    FileStat,
    Owner,
    OwnerKind,
    posix_group,
    posix_owner,
    windows_owner,
)
from .summary import IncompleteIndexError

if TYPE_CHECKING:
    from collections.abc import Iterator

_DIRS = "SELECT id, parent_id, name FROM dirs ORDER BY id"

_FILES = """
SELECT f.dir_id, f.name, f.name_raw, f.size, f.blocks, f.mtime, f.btime, f.atime,
       o.kind, o.raw_id, g.kind, g.raw_id,
       f.mode, f.ino, f.dev, f.nlink, f.islink, f.link_target
FROM files f
LEFT JOIN owners o ON o.id = f.owner_id
LEFT JOIN owners g ON g.id = f.group_id
ORDER BY f.dir_id, f.id
"""


def iter_dir_listings(con: sqlite3.Connection) -> Iterator[DirListing]:
    """Yield every directory in the index as a reconstructed ``DirListing``.

    Runs a fixed number of queries regardless of tree size, not one per
    directory. Paths and child names are built for the whole tree up front —
    bounded by the number of directories, which is small — but files are
    streamed one directory at a time, so a tree's entire set of ``FileStat``s
    is never resident at once.

    Args:
        con (sqlite3.Connection): An open connection to a finished index.

    Yields:
        DirListing: One reconstructed listing per directory in the scan.

    Raises:
        IncompleteIndexError: If the index holds no finished scan.
    """
    rows, paths = _dir_paths(con)

    subdirs: dict[int, set[str]] = defaultdict(set)
    for dir_id, parent_id, _name in rows:
        if parent_id is not None:
            subdirs[parent_id].add(paths[dir_id].name)

    # Both queries are ordered by (a prefix of) dir id, so a directory's files
    # are always the next group here by the time its own row comes around in
    # the loop below — never further ahead than one directory at a time.
    file_groups = groupby(con.execute(_FILES), key=lambda row: row[0])
    next_group: tuple[int, Iterator[tuple]] | None = next(file_groups, None)

    for dir_id, _parent_id, _name in rows:
        files: tuple[FileStat, ...] = ()
        if next_group is not None and next_group[0] == dir_id:
            files = tuple(_build_file(paths[dir_id], row) for row in next_group[1])
            next_group = next(file_groups, None)
        yield DirListing.build(
            path=paths[dir_id],
            files=files,
            subdirs=subdirs.get(dir_id, set()),
        )


def _dir_paths(con: sqlite3.Connection) -> tuple[list[tuple], dict[int, PurePath]]:
    """Every directory row, and each row's id mapped to its reconstructed path.

    Args:
        con (sqlite3.Connection): An open connection to a finished index.

    Returns:
        tuple[list[tuple], dict[int, PurePath]]: The raw ``(id, parent_id, name)``
            rows in scan order, and each id mapped to its reconstructed path.

    Raises:
        IncompleteIndexError: If the index holds no finished scan.
    """
    root, platform_name = _finished_scan(con)
    path_cls: type[PurePath] = (
        PureWindowsPath if platform_name == "Windows" else PurePosixPath
    )

    rows = con.execute(_DIRS).fetchall()

    paths: dict[int, PurePath] = {}
    for dir_id, parent_id, name in rows:
        paths[dir_id] = path_cls(root) if parent_id is None else paths[parent_id] / name

    return rows, paths


def dir_ids_by_path(con: sqlite3.Connection) -> dict[PurePath, int]:
    """Every directory's reconstructed path mapped to its row id.

    A companion to :func:`iter_dir_listings` for callers that need to look a
    directory back up by path afterwards — classification uses this to find
    the id behind a hit's listing, so it can query that directory's subtree.

    Args:
        con (sqlite3.Connection): An open connection to a finished index.

    Returns:
        dict[PurePath, int]: Every directory's path mapped to its row id.

    Raises:
        IncompleteIndexError: If the index holds no finished scan.
    """
    _rows, paths = _dir_paths(con)
    return {path: dir_id for dir_id, path in paths.items()}


def subtree_size(con: sqlite3.Connection, dir_id: int) -> int:
    """Total file size at ``dir_id`` and every directory beneath it.

    One recursive query scoped to this directory's own subtree — meant to be
    called per finding, not per directory in the whole tree, since findings
    are typically a small fraction of all directories.

    Args:
        con (sqlite3.Connection): An open connection to a finished index.
        dir_id (int): The directory's row id.

    Returns:
        int: Total bytes across dir_id and its descendants.
    """
    (total,) = con.execute(
        """
        WITH RECURSIVE subtree(id) AS (
            SELECT ?
            UNION ALL
            SELECT d.id FROM dirs d JOIN subtree s ON d.parent_id = s.id
        )
        SELECT COALESCE(SUM(f.size), 0) FROM files f WHERE f.dir_id IN (SELECT id FROM subtree)
        """,
        (dir_id,),
    ).fetchone()
    return total


def _build_file(dir_path: PurePath, row: tuple) -> FileStat:
    """Build one ``FileStat`` from a joined ``files`` row.

    Args:
        dir_path (PurePath): The already-reconstructed path of the containing directory.
        row (tuple): One row from the ``_FILES`` query.

    Returns:
        FileStat: The reconstructed file record.
    """
    (
        _dir_id,
        name,
        name_raw,
        size,
        blocks,
        mtime,
        btime,
        atime,
        owner_kind,
        owner_raw_id,
        group_kind,
        group_raw_id,
        mode,
        ino,
        dev,
        nlink,
        islink,
        link_target,
    ) = row
    return FileStat(
        path=dir_path / name,
        size=size,
        blocks=blocks,
        mtime=mtime,
        btime=btime,
        atime=atime,
        owner=_owner(owner_kind, owner_raw_id),
        group=_owner(group_kind, group_raw_id),
        mode=mode,
        ino=ino,
        dev=dev,
        nlink=nlink,
        islink=bool(islink),
        link_target=link_target,
        name_raw=name_raw,
    )


def _finished_scan(con: sqlite3.Connection) -> tuple[str, str | None]:
    """Look up the one finished scan's root and recorded platform.

    Args:
        con (sqlite3.Connection): An open connection to an index.

    Returns:
        tuple[str, str | None]: The scan's root path string and platform name.

    Raises:
        IncompleteIndexError: If no scan in this index has finished.
    """
    scan = con.execute(
        "SELECT root, platform FROM scans"
        " WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if scan is None:
        raise IncompleteIndexError(
            "this index holds no finished scan — it was probably interrupted. "
            "Delete it and scan again."
        )
    return scan


def _owner(kind: str | None, raw_id: str | None) -> Owner | None:
    """Reconstruct an interned ``Owner`` from its stored kind and raw id.

    Args:
        kind (str | None): The ``OwnerKind`` value stored for this principal, or None.
        raw_id (str | None): The raw uid/gid/SID string, or None.

    Returns:
        Owner | None: The interned owner, or None if kind was None.
    """
    if kind is None:
        return None
    if kind == OwnerKind.POSIX.value:
        return posix_owner(int(raw_id))
    if kind == OwnerKind.POSIX_GROUP.value:
        return posix_group(int(raw_id))
    return windows_owner(raw_id)
