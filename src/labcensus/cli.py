"""Command line interface.

The summary goes to stdout and progress goes to stderr, so a scan can be piped
somewhere without progress lines contaminating it.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import typer

from .classify import Rollup, classify_index
from .detectors import load_detectors
from .index import IndexWriter
from .index.summary import IncompleteIndexError, Summary, human_size, summarise
from .index.writer import IndexTargetInsideTreeError, check_target_outside
from .walker import walk

DEFAULT_INDEX_NAME = f"labcensus-index-{time.strftime('%Y%m%d-%H%M%S')}.db"

app = typer.Typer(
    help="labcensus — read-only census of a lab's storage.",
    add_completion=False,
)


@app.callback()
def main() -> None:
    """labcensus — read-only census of a lab's storage."""


scan_output = typer.Option(
    None,
    "--output",
    "-o",
    help=f"Where to write the index (default: ./{DEFAULT_INDEX_NAME}).",
)

scan_progress = typer.Option(
    None,
    "--progress/--no-progress",
    help="Progress to stderr. On by default when stderr is a terminal.",
)


@app.command()
def scan(
    path: str = typer.Argument(..., help="Directory to scan."),
    output: Path = scan_output,
    top: int = typer.Option(10, "--top", help="Rows in each summary table."),
    progress: bool = scan_progress,
) -> None:
    """
    Walk PATH and record what is on it.

    Reads only. Nothing is written inside PATH, and nothing leaves this machine.

    Args:
        path (str): Directory to scan.
        output (Path): Where to write the index (default: ./labcensus-index-YYYYMMDD-HHMMSS.db).
        top (int): Rows in each summary table.
        progress (bool): Progress to stderr. On by default when stderr is a terminal.

    Returns:
        None

    Raises:
        typer.Exit: If the path is not a directory, if the index would be written inside
        the tree being scanned, or if the index already exists.
    """

    root = Path(path)
    if not root.is_dir():
        typer.secho(f"not a directory: {root}", fg="red", err=True)
        raise typer.Exit(2)

    # Database for saving the scan. If the user didn't specify a path, use the current working
    db_path = Path(output) if output else Path.cwd() / DEFAULT_INDEX_NAME
    try:
        check_target_outside(db_path, root)
    except IndexTargetInsideTreeError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc

    # If the index already exists, don't overwrite it. The user can remove it or choose another path.
    if db_path.exists():
        typer.secho(
            f"index already exists: {db_path}\n"
            "remove it, or choose another path with -o",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)

    show_progress = sys.stderr.isatty() if progress is None else progress
    started = time.perf_counter()

    with IndexWriter(db_path) as writer:
        walk(root, writer, progress=_progress if show_progress else None)

    elapsed = time.perf_counter() - started
    if show_progress:
        print(file=sys.stderr)

    con = sqlite3.connect(db_path)
    try:
        _render(summarise(con, top_n=top), elapsed=elapsed, db_path=db_path)
    finally:
        con.close()


@app.command()
def classify(
    index: str = typer.Argument(..., help="Index to classify (written by `scan -o`)."),
    top: int = typer.Option(10, "--top", help="Rows in each summary table."),
    plugins: bool = typer.Option(
        False,
        "--plugins/--no-plugins",
        help="Also load third-party detectors registered under labcensus.detectors. "
        "Off by default: this executes arbitrary installed code.",
    ),
) -> None:
    """
    Detect known data formats across every directory in INDEX, and roll up what was found.

    Reads only the index. The storage that produced it is not touched again.

    Args:
        index (str): Index to classify (written by `scan -o`).
        top (int): Rows in each summary table.
        plugins (bool): Also load third-party detectors registered under labcensus.detectors.

    Returns:
        None

    Raises:
        typer.Exit: If the index file does not exist, is not a readable index,
            or holds no finished scan.
    """

    db_path = Path(index)
    if not db_path.is_file():
        typer.secho(f"not a file: {db_path}", fg="red", err=True)
        raise typer.Exit(2)

    detectors = load_detectors(include_plugins=plugins)

    con = sqlite3.connect(db_path)
    try:
        rollup = classify_index(con, detectors=detectors, top_n=top)
    except sqlite3.DatabaseError as exc:
        typer.secho(f"not a valid index: {db_path} ({exc})", fg="red", err=True)
        raise typer.Exit(2) from exc
    except IncompleteIndexError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    finally:
        con.close()

    _render_classify(rollup, index_path=db_path, n_detectors=len(detectors))


def _progress(*, dirs: int, files: int, errors: int, path: str) -> None:
    """
    Logs the last 48 characters of the path, so the user sees where the scan
    is up to without being overwhelmed by long paths.

    Args:
        dirs (int): Number of directories scanned so far.
        files (int): Number of files scanned so far.
        errors (int): Number of unreadable files encountered so far.
        path (str): The current path being scanned.

    Returns:
        None
    """

    tail = path if len(path) <= 48 else "…" + path[-47:]
    print(
        f"\r  {dirs:,} dirs  {files:,} files  {errors:,} unreadable   {tail}",
        end="",
        file=sys.stderr,
        flush=True,
    )


def _render(summary: Summary, *, elapsed: float, db_path: Path) -> None:
    """
    Render the summary to stdout.

    Args:
        summary (Summary): The summary of the scan.
        elapsed (float): Time taken to perform the scan.
        db_path (Path): Path to the database where the index was written.

    Returns:
        None
    """

    echo = typer.echo

    echo(f"\n{summary.root}")
    echo(
        f"  {summary.n_files:,} files in {summary.n_dirs:,} directories, "
        f"{summary.max_depth} deep, scanned in {elapsed:.1f}s"
    )

    line = f"  {human_size(summary.total_size)}"
    if summary.total_allocated is not None:
        line += f" apparent, {human_size(summary.total_allocated)} on disk"
    echo(line)

    details = [f"{summary.n_owners} owner(s)"]
    if summary.n_symlinks:
        details.append(f"{summary.n_symlinks:,} symlink(s)")
    if summary.n_errors:
        details.append(f"{summary.n_errors:,} unreadable")
    echo("  " + ", ".join(details))

    if summary.top_suffixes:
        echo("\nBy file type")
        for row in summary.top_suffixes:
            echo(
                f"  {row.suffix:<18} {row.count:>9,} files  {human_size(row.size):>12}"
            )

    if summary.largest_dirs:
        echo("\nLargest directories")
        for row in summary.largest_dirs:
            echo(f"  {human_size(row.size):>12}  {row.count:>7,} files  {row.path}")

    if summary.n_files:
        echo("\nBy age (last modified)")
        for age in summary.ages:
            if age.count:
                echo(
                    f"  {age.label:<18} {age.count:>9,} files  "
                    f"{human_size(age.size):>12}"
                )

    if summary.sample_errors:
        echo("\nCould not be read")
        for err_path, reason in summary.sample_errors:
            echo(f"  {reason}: {err_path}")
        if summary.n_errors > len(summary.sample_errors):
            echo(f"  … and {summary.n_errors - len(summary.sample_errors):,} more")

    echo(f"\nIndex written to {db_path}")
    echo("Nothing was written inside the scanned tree, and nothing left this machine.")


def _render_classify(rollup: Rollup, *, index_path: Path, n_detectors: int) -> None:
    """
    Render the classification rollup to stdout.

    Args:
        rollup (Rollup): The classification rollup.
        index_path (Path): Path to the index that was classified.
        n_detectors (int): How many detectors were run.

    Returns:
        None
    """

    echo = typer.echo

    echo(f"\n{rollup.root}")
    echo(f"  classified from {index_path}")
    echo(
        f"  {rollup.n_dirs:,} directories, "
        f"{rollup.n_dirs_with_hits:,} matched a known format, "
        f"{rollup.n_dirs_without_hits:,} did not"
    )
    echo(
        f"  {human_size(rollup.size_with_hits)} matched, "
        f"{human_size(rollup.size_without_hits)} unrecognised, "
        f"{human_size(rollup.total_size)} total"
    )

    if rollup.detectors:
        echo(f"\nBy detector ({n_detectors} loaded)")
        for row in rollup.detectors:
            echo(
                f"  {row.detector:<16} {row.modality:<40} "
                f"{row.n_dirs:>6,} dirs  {human_size(row.total_size):>12}"
            )
            bits = [
                f"{label} {n}"
                for label, n in (
                    ("HIGH", row.confidence.high),
                    ("MEDIUM", row.confidence.medium),
                    ("LOW", row.confidence.low),
                )
                if n
            ]
            if bits:
                echo("      " + "   ".join(bits))
            for sample in row.samples:
                echo(f"      {sample.path}")
            if row.n_dirs > len(row.samples):
                echo(f"      … and {row.n_dirs - len(row.samples):,} more")
    else:
        echo("\nNo directory matched a known format.")

    echo(
        f"\nUnrecognised: {rollup.n_dirs_without_hits:,} directories, "
        f"{human_size(rollup.size_without_hits)}"
    )
    if rollup.sample_unrecognised:
        for row in rollup.sample_unrecognised:
            echo(f"  {human_size(row.size):>12}  {row.path}")
        if rollup.n_dirs_without_hits > len(rollup.sample_unrecognised):
            more = rollup.n_dirs_without_hits - len(rollup.sample_unrecognised)
            echo(f"  … and {more:,} more")

    echo(
        "\nRead-only: nothing was written to the index, and nothing left this machine."
    )


if __name__ == "__main__":
    app()
