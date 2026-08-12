"""Traversal: walk a tree once, write every path into the index.

Breadth-first over an explicit queue rather than recursion, because lab trees
nest deeply enough — per-session, per-probe, per-plane directories — to make
recursion depth a real question, and because an explicit queue is what
resumability will need when it arrives.

Symlinks are recorded and never followed. Following them means loops and
double-counting, and a tree full of links into somebody else's share is itself
a census finding rather than something to chase.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .backends import LocalBackend

if TYPE_CHECKING:
    from .index import IndexWriter


class ProgressReporter(Protocol):
    """Called as the walk proceeds.

    A scan of ten million files over SMB runs for around an hour. An hour of
    silence reads as a hang, so progress is a requirement rather than a
    courtesy — it just does not belong in this module.
    """

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

    Returns ``(dirs, files, errors)``. Nothing is raised for an unreadable
    path: a NAS produces permission errors within seconds, and a scan that stops
    at the first one is useless on the only hardware that matters. They are
    recorded and the walk continues.
    """
    backend = backend or LocalBackend()
    root_path = Path(root)
    root_pure = backend.to_pure(root_path)

    writer.begin_scan(root_pure)

    n_dirs = n_files = n_errors = 0

    # (filesystem path, parent row id, depth). Explicit queue, not recursion.
    queue: list[tuple[Path, int | None, int]] = [(root_path, None, 0)]

    while queue:
        current, parent_id, depth = queue.pop()
        listing, errors = backend.list_dir(current)

        if errors:
            writer.add_errors(errors)
            n_errors += len(errors)

        if listing is None:
            # The directory itself could not be read. Already recorded as an
            # error; there is nothing beneath it to enqueue.
            continue

        name = listing.path.name or str(listing.path)
        dir_id = writer.add_dir(parent_id, name, depth)
        n_dirs += 1

        writer.add_files(dir_id, listing.files)
        n_files += len(listing.files)

        # Reversed so that popping from the end yields sorted order, keeping the
        # walk deterministic across runs and filesystems.
        for child in reversed(list(backend.subdir_paths(listing))):
            queue.append((Path(str(child)), dir_id, depth + 1))

        if progress and n_dirs % progress_every == 0:
            progress(
                dirs=n_dirs, files=n_files, errors=n_errors, path=str(listing.path)
            )

    if progress:
        progress(dirs=n_dirs, files=n_files, errors=n_errors, path=str(root_pure))

    return n_dirs, n_files, n_errors
