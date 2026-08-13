"""Streaming writer for the scan index."""

from __future__ import annotations

import platform
import socket
import sqlite3
import time
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, TypeVar

from .. import __version__
from .schema import (
    BUILD_PRAGMAS,
    INDEX_SCHEMA_VERSION,
    INDEXES,
    QUERY_PRAGMAS,
    TABLES,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..types import FileStat, Owner, WalkError

#: Rows buffered before a flush.
BATCH_SIZE = 10_000

# typing.Self is 3.11+; labcensus supports 3.10.
_Self = TypeVar("_Self", bound="IndexWriter")


class IndexTargetInsideTreeError(Exception):
    """The index would be written inside the tree being scanned."""


class ScanAlreadyRecordedError(Exception):
    """This index already holds a scan. One index file, one scan."""


def check_target_outside(db_path: Path, root: Path) -> None:
    """Raise if ``db_path`` resolves to somewhere inside ``root``.

    Args:
        db_path (Path): Where the index would be written.
        root (Path): The directory about to be scanned.

    Returns:
        None

    Raises:
        IndexTargetInsideTreeError: If db_path is root itself or lands inside it.
    """
    db_resolved = db_path.expanduser().resolve()
    root_resolved = root.expanduser().resolve()
    if db_resolved == root_resolved or root_resolved in db_resolved.parents:
        raise IndexTargetInsideTreeError(
            f"refusing to write the index to {db_resolved}, which is inside the "
            f"tree being scanned ({root_resolved}). Choose a path outside it."
        )


class IndexWriter:
    """Builds one scan's index. Use as a context manager.

    An index holds exactly one scan. If the block exits with an exception the
    database is removed, so an interrupted run leaves nothing behind to trip
    over on the next attempt.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        batch_size: int = BATCH_SIZE,
        hostname: str | None = None,
        now: float | None = None,
    ) -> None:
        """
        Args:
            db_path (str | Path): Where to write the index.
            batch_size (int): Rows buffered before a flush.
            hostname (str | None): Overrides the detected hostname; lets tests pin it.
            now (float | None): Overrides the detected clock; lets tests pin it.

        Returns:
            None
        """
        self.db_path = Path(db_path)
        self._batch_size = batch_size
        # Injectable so tests can pin the non-deterministic fields.
        self._hostname = hostname if hostname is not None else socket.gethostname()
        self._now = now
        self._con: sqlite3.Connection | None = None
        self._scan_id: int | None = None
        self._files: list[tuple] = []
        self._errors: list[tuple] = []
        self._owners: dict[Owner, int] = {}
        self._suffixes: dict[str, int] = {}
        self._preexisting = False
        self._n_dirs = 0
        self._n_files = 0
        self._n_errors = 0

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self: _Self) -> _Self:  # noqa: PYI019
        self._preexisting = self.db_path.exists()
        self._con = sqlite3.connect(self.db_path)
        self._con.executescript(BUILD_PRAGMAS)
        self._con.executescript(TABLES)
        self._con.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            ("index_schema_version", str(INDEX_SCHEMA_VERSION)),
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._con is None:
            return
        try:
            if exc_type is None:
                self.finish()
        finally:
            self._con.close()
            self._con = None
            if exc_type is not None and not self._preexisting:
                self.db_path.unlink(missing_ok=True)

    @property
    def connection(self) -> sqlite3.Connection:
        if self._con is None:
            raise RuntimeError("IndexWriter must be used as a context manager")
        return self._con

    def begin_scan(self, root: PurePath) -> int | None:
        """Open a scan and return its id.

        Args:
            root (PurePath): The directory being scanned.

        Returns:
            int: The new scan's row id.

        Raises:
            ScanAlreadyRecordedError: If this index already holds one; the
                rollups assume a single scan per file.
        """
        if self.connection.execute("SELECT 1 FROM scans LIMIT 1").fetchone():
            raise ScanAlreadyRecordedError(
                f"{self.db_path} already holds a scan; use a new index file"
            )
        cur = self.connection.execute(
            "INSERT INTO scans(root, started_at, tool_version, hostname, platform) "
            "VALUES(?,?,?,?,?)",
            (
                str(root),
                self._now if self._now is not None else time.time(),
                __version__,
                self._hostname,
                platform.system(),
            ),
        )
        self._scan_id = cur.lastrowid
        return self._scan_id

    def finish(self) -> None:
        """Flush, record counts, build indexes, and mark the scan finished.

        Returns:
            None
        """
        self.flush()
        con = self.connection
        con.execute(
            "UPDATE scans SET finished_at=?, n_dirs=?, n_files=?, n_errors=? WHERE id=?",
            (
                self._now if self._now is not None else time.time(),
                self._n_dirs,
                self._n_files,
                self._n_errors,
                self._scan_id,
            ),
        )
        con.commit()
        con.executescript(INDEXES)
        con.commit()
        con.executescript(QUERY_PRAGMAS)

    # -- writing -----------------------------------------------------------

    def add_dir(self, parent_id: int | None, name: str, depth: int) -> int:
        """Record a directory and return the id its files reference.

        Args:
            parent_id (int | None): The parent directory's row id, or None for the scan root.
            name (str): The directory's own name.
            depth (int): How many levels below the scan root this directory sits.

        Returns:
            int: The new directory's row id.
        """
        cur = self.connection.execute(
            "INSERT INTO dirs(scan_id, parent_id, name, depth) VALUES(?,?,?,?)",
            (self._scan_id, parent_id, name, depth),
        )
        self._n_dirs += 1
        return cur.lastrowid #type: ignore

    def add_files(self, dir_id: int, files: Iterable[FileStat]) -> None:
        """
        Args:
            dir_id (int): The containing directory's row id.
            files (Iterable[FileStat]): The files to record.

        Returns:
            None
        """
        for stat in files:
            self._files.append(
                (
                    dir_id,
                    stat.name,
                    stat.name_raw,
                    self._suffix_id(stat.suffix),
                    stat.size,
                    stat.blocks,
                    stat.mtime,
                    stat.btime,
                    stat.atime,
                    self._owner_id(stat.owner),
                    self._owner_id(stat.group),
                    stat.mode,
                    stat.ino,
                    stat.dev,
                    stat.nlink,
                    int(stat.islink),
                    stat.link_target,
                )
            )
            self._n_files += 1
        if len(self._files) >= self._batch_size:
            self.flush()

    def add_errors(self, errors: Iterable[WalkError]) -> None:
        """
        Args:
            errors (Iterable[WalkError]): The unreadable paths to record.

        Returns:
            None
        """
        for error in errors:
            self._errors.append((self._scan_id, str(error.path), error.reason))
            self._n_errors += 1
        if len(self._errors) >= self._batch_size:
            self.flush()

    def flush(self) -> None:
        """Write buffered rows to the database and clear the buffers.

        Returns:
            None
        """
        con = self.connection
        if self._files:
            con.executemany(
                "INSERT INTO files(dir_id, name, name_raw, suffix_id, size, blocks,"
                " mtime, btime, atime, owner_id, group_id, mode, ino, dev, nlink,"
                " islink, link_target)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._files,
            )
            self._files.clear()
        if self._errors:
            con.executemany(
                "INSERT INTO errors(scan_id, path, reason) VALUES(?,?,?)",
                self._errors,
            )
            self._errors.clear()
        con.commit()

    # -- interning ---------------------------------------------------------

    def _owner_id(self, owner: Owner | None) -> int | None:
        """Intern owner and return its row id, inserting if new.

        Args:
            owner (Owner | None): The principal to intern, or None.

        Returns:
            int | None: The owner's row id, or None if owner was None.
        """
        if owner is None:
            return None
        known = self._owners.get(owner)
        if known is not None:
            return known
        cur = self.connection.execute(
            "INSERT OR IGNORE INTO owners(kind, raw_id) VALUES(?,?)",
            (owner.kind.value, owner.id),
        )
        if cur.lastrowid and cur.rowcount:
            owner_id = cur.lastrowid
        else:
            owner_id = self.connection.execute(
                "SELECT id FROM owners WHERE kind=? AND raw_id=?",
                (owner.kind.value, owner.id),
            ).fetchone()[0]
        self._owners[owner] = owner_id
        return owner_id

    def _suffix_id(self, suffix: str) -> int | None:
        """Intern suffix and return its row id, inserting if new.

        Args:
            suffix (str): The file extension to intern, including the dot.

        Returns:
            int | None: The suffix's row id, or None if suffix was empty.
        """
        if not suffix:
            return None
        known = self._suffixes.get(suffix)
        if known is not None:
            return known
        cur = self.connection.execute(
            "INSERT OR IGNORE INTO suffixes(value) VALUES(?)", (suffix,)
        )
        if cur.lastrowid and cur.rowcount:
            suffix_id = cur.lastrowid
        else:
            suffix_id = self.connection.execute(
                "SELECT id FROM suffixes WHERE value=?", (suffix,)
            ).fetchone()[0]
        self._suffixes[suffix] = suffix_id
        return suffix_id
