"""Walk a tree once, writing every path into the index.

Depth-first over an explicit stack rather than recursion, so deeply nested
trees cannot exhaust the interpreter's stack.

Symlinks are recorded but never followed, which avoids loops and
double-counting.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .backends import LocalBackend

if TYPE_CHECKING:
    from .index import IndexWriter


class ProgressReporter(Protocol):
    """Called periodically as the walk proceeds."""

    def __call__(self, *, dirs: int, files: int, errors: int, path: str) -> None: ...


def walk(
    root: str | Path,
    writer: IndexWriter,
    *,
    backend: LocalBackend | None = None,
    progress: ProgressReporter | None = None,
    progress_every: int = 1000,
) -> tuple[int, int, int]:
    """Walk ``root``, writing every directory and file into ``writer``.

    Returns ``(dirs, files, errors)``. Unreadable paths are recorded and the
    walk continues rather than stopping.
    """
    backend = backend or LocalBackend()
    root_path = Path(root)
    root_pure = backend.to_pure(root_path)

    writer.begin_scan(root_pure)

    n_dirs = n_files = n_errors = 0

    # (filesystem path, parent row id, depth)
    stack: list[tuple[Path, int | None, int]] = [(root_path, None, 0)]

    while stack:
        current, parent_id, depth = stack.pop()
        listing, errors = backend.list_dir(current)

        if errors:
            writer.add_errors(errors)
            n_errors += len(errors)

        if listing is None:
            # Recorded as an error above; nothing beneath it to visit.
            continue

        name = listing.path.name or str(listing.path)
        dir_id = writer.add_dir(parent_id, name, depth)
        n_dirs += 1

        writer.add_files(dir_id, listing.files)
        n_files += len(listing.files)

        # Reversed so that popping yields sorted order.
        for child in reversed(list(backend.subdir_paths(listing))):
            stack.append((Path(str(child)), dir_id, depth + 1))

        if progress and n_dirs % progress_every == 0:
            progress(
                dirs=n_dirs, files=n_files, errors=n_errors, path=str(listing.path)
            )

    if progress:
        progress(dirs=n_dirs, files=n_files, errors=n_errors, path=str(root_pure))

    return n_dirs, n_files, n_errors
