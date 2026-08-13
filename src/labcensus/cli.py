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

from .index import IndexWriter
from .index.summary import Summary, human_size, summarise
from .index.writer import IndexTargetInsideTreeError, check_target_outside
from .walker import walk

DEFAULT_INDEX_NAME = "labcensus-index.db"

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
    """

    root = Path(path)
    if not root.is_dir():
        typer.secho(f"not a directory: {root}", fg="red", err=True)
        raise typer.Exit(2)

    db_path = Path(output) if output else Path.cwd() / DEFAULT_INDEX_NAME
    try:
        check_target_outside(db_path, root)
    except IndexTargetInsideTreeError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc

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


def _progress(*, dirs: int, files: int, errors: int, path: str) -> None:
    tail = path if len(path) <= 48 else "…" + path[-47:]
    print(
        f"\r  {dirs:,} dirs  {files:,} files  {errors:,} unreadable   {tail}",
        end="",
        file=sys.stderr,
        flush=True,
    )


def _render(summary: Summary, *, elapsed: float, db_path: Path) -> None:
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


if __name__ == "__main__":
    app()
