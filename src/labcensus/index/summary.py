"""Rollups over a finished index."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

#: Rows shown in each top-N table.
TOP_N = 10

#: Upper bound in days for each bucket, with everything older in the last.
AGE_BUCKETS = (
    (30, "< 1 month"),
    (365, "1 month – 1 year"),
    (1095, "1 – 3 years"),
)
AGE_OLDEST = "> 3 years"

_DIR_PATHS = """
WITH RECURSIVE tree(id, path) AS (
    SELECT id, name FROM dirs WHERE parent_id IS NULL
    UNION ALL
    SELECT d.id, tree.path || '/' || d.name
    FROM dirs d JOIN tree ON d.parent_id = tree.id
)
"""


class IncompleteIndexError(Exception):
    """The index holds no finished scan."""


@dataclass(frozen=True)
class SuffixRow:
    suffix: str
    count: int
    size: int


@dataclass(frozen=True)
class DirRow:
    path: str
    count: int
    size: int


@dataclass(frozen=True)
class AgeRow:
    label: str
    count: int
    size: int


@dataclass(frozen=True)
class Summary:
    """What a scan found, ready to render."""

    root: str
    n_dirs: int
    n_files: int
    n_errors: int
    total_size: int
    total_allocated: int | None
    newest_mtime: float | None
    oldest_mtime: float | None
    n_symlinks: int
    n_owners: int
    max_depth: int
    top_suffixes: tuple[SuffixRow, ...]
    largest_dirs: tuple[DirRow, ...]
    ages: tuple[AgeRow, ...]
    sample_errors: tuple[tuple[str, str], ...]


def summarise(
    con: sqlite3.Connection, *, top_n: int = TOP_N, now: float | None = None
) -> Summary:
    """Summarise the scan in ``con``.

    Raises :class:`IncompleteIndexError` if the index holds no finished scan,
    which is what an interrupted run leaves behind.
    """
    now = time.time() if now is None else now
    scan = con.execute(
        "SELECT root, n_dirs, n_files, n_errors FROM scans"
        " WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if scan is None:
        raise IncompleteIndexError(
            "this index holds no finished scan — it was probably interrupted. "
            "Delete it and scan again."
        )
    root, n_dirs, n_files, n_errors = scan

    total_size, total_allocated, newest, oldest = con.execute(
        "SELECT COALESCE(SUM(size),0), SUM(blocks), MAX(mtime), MIN(mtime) FROM files"
    ).fetchone()

    (n_symlinks,) = con.execute("SELECT COUNT(*) FROM files WHERE islink=1").fetchone()
    (n_owners,) = con.execute(
        "SELECT COUNT(*) FROM owners WHERE kind != 'posix_group'"
    ).fetchone()
    (max_depth,) = con.execute("SELECT COALESCE(MAX(depth),0) FROM dirs").fetchone()

    return Summary(
        root=root,
        n_dirs=n_dirs or 0,
        n_files=n_files or 0,
        n_errors=n_errors or 0,
        total_size=total_size,
        total_allocated=None if total_allocated is None else total_allocated * 512,
        newest_mtime=newest,
        oldest_mtime=oldest,
        n_symlinks=n_symlinks,
        n_owners=n_owners,
        max_depth=max_depth,
        top_suffixes=_top_suffixes(con, top_n),
        largest_dirs=_largest_dirs(con, top_n),
        ages=_ages(con, now),
        sample_errors=tuple(
            con.execute("SELECT path, reason FROM errors LIMIT ?", (top_n,)).fetchall()
        ),
    )


def _top_suffixes(con: sqlite3.Connection, top_n: int) -> tuple[SuffixRow, ...]:
    """The largest file types by total size."""
    rows = con.execute(
        "SELECT COALESCE(s.value, '(no extension)'), COUNT(*), SUM(f.size)"
        " FROM files f LEFT JOIN suffixes s ON s.id = f.suffix_id"
        " GROUP BY f.suffix_id ORDER BY SUM(f.size) DESC LIMIT ?",
        (top_n,),
    ).fetchall()
    return tuple(SuffixRow(*row) for row in rows)


def _largest_dirs(con: sqlite3.Connection, top_n: int) -> tuple[DirRow, ...]:
    """The largest directories, counting only the files directly in each.

    Subtree totals are a different question and a separate query.
    """
    rows = con.execute(
        _DIR_PATHS + "SELECT t.path, COUNT(f.id), COALESCE(SUM(f.size),0)"
        " FROM files f JOIN tree t ON t.id = f.dir_id"
        " GROUP BY f.dir_id ORDER BY SUM(f.size) DESC LIMIT ?",
        (top_n,),
    ).fetchall()
    return tuple(DirRow(*row) for row in rows)


def _ages(con: sqlite3.Connection, now: float) -> tuple[AgeRow, ...]:
    """File counts and sizes per age band, oldest last.

    The bands cover every file, including any whose timestamp is in the future
    — clock skew and restored timestamps both produce those, and a band that
    quietly dropped them would not add up to the total.
    """
    out = []
    upper: float | None = None
    for days, label in AGE_BUCKETS:
        cutoff = now - days * 86400
        if upper is None:
            count, size = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(size),0) FROM files WHERE mtime > ?",
                (cutoff,),
            ).fetchone()
        else:
            count, size = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(size),0) FROM files"
                " WHERE mtime > ? AND mtime <= ?",
                (cutoff, upper),
            ).fetchone()
        out.append(AgeRow(label, count, size))
        upper = cutoff

    count, size = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(size),0) FROM files WHERE mtime <= ?", (upper,)
    ).fetchone()
    out.append(AgeRow(AGE_OLDEST, count, size))
    return tuple(out)


def human_size(n: int | None) -> str:
    """Bytes as a short human-readable string, or an em dash if unknown."""
    if n is None:
        return "—"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return f"{value:,.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:,.1f} TB"
