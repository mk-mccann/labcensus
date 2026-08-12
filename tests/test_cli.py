"""The CLI surface, including the guardrails that protect the read-only claim."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from labcensus.cli import app

runner = CliRunner()


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "lab"
    (root / "rec1").mkdir(parents=True)
    (root / "rec1" / "structure.oebin").write_text("{}")
    (root / "rec1" / "continuous.dat").write_bytes(b"\x00" * 4096)
    (root / "notes.txt").write_text("hello")
    return root


def scan(tree, tmp_path, *extra):
    return runner.invoke(
        app,
        ["scan", str(tree), "-o", str(tmp_path / "out.db"), "--no-progress", *extra],
    )


class TestScanCommand:
    def test_scan_is_a_subcommand(self):
        # Typer collapses a single-command app, which silently made
        # `labcensus scan PATH` - the documented form - not exist.
        result = runner.invoke(app, ["scan", "--help"])
        assert result.exit_code == 0
        assert "Walk PATH" in result.stdout

    def test_reports_what_is_there(self, tree, tmp_path):
        result = scan(tree, tmp_path)
        assert result.exit_code == 0
        assert "3 files in 2 directories" in result.stdout
        assert ".dat" in result.stdout
        assert "Largest directories" in result.stdout

    def test_reports_where_the_index_went(self, tree, tmp_path):
        result = scan(tree, tmp_path)
        assert str(tmp_path / "out.db") in result.stdout
        assert (tmp_path / "out.db").exists()

    def test_top_bounds_each_table(self, tree, tmp_path):
        result = scan(tree, tmp_path, "--top", "1")
        assert result.exit_code == 0
        # Two suffixes exist (.dat, .txt); only one row is shown.
        assert result.stdout.count(" files  ") < 6


class TestReadOnly:
    def test_nothing_is_written_inside_the_scanned_tree(self, tree, tmp_path):
        before = {p: p.stat().st_mtime_ns for p in tree.rglob("*")}
        scan(tree, tmp_path)
        after = {p: p.stat().st_mtime_ns for p in tree.rglob("*")}
        assert before == after, "the scan altered the tree it was reading"

    def test_index_inside_the_scanned_tree_is_refused(self, tree):
        result = runner.invoke(
            app, ["scan", str(tree), "-o", str(tree / "census.db"), "--no-progress"]
        )
        assert result.exit_code == 2
        assert "refusing" in result.output
        assert not (tree / "census.db").exists()

    def test_index_in_a_subdirectory_of_the_tree_is_also_refused(self, tree):
        result = runner.invoke(
            app,
            [
                "scan",
                str(tree),
                "-o",
                str(tree / "rec1" / "census.db"),
                "--no-progress",
            ],
        )
        assert result.exit_code == 2
        assert "refusing" in result.output


class TestFailureModes:
    def test_missing_directory_exits_cleanly(self, tmp_path):
        result = runner.invoke(app, ["scan", str(tmp_path / "nope"), "--no-progress"])
        assert result.exit_code == 2
        assert "not a directory" in result.output

    def test_a_file_is_not_a_directory(self, tmp_path):
        target = tmp_path / "a.txt"
        target.write_text("x")
        result = runner.invoke(app, ["scan", str(target), "--no-progress"])
        assert result.exit_code == 2

    def test_existing_index_is_not_silently_overwritten(self, tree, tmp_path):
        (tmp_path / "out.db").write_text("previous scan")
        result = scan(tree, tmp_path)
        assert result.exit_code == 2
        assert "already exists" in result.output
        assert (tmp_path / "out.db").read_text() == "previous scan"

    def test_empty_directory_produces_a_report_not_a_crash(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = runner.invoke(
            app,
            ["scan", str(empty), "-o", str(tmp_path / "e.db"), "--no-progress"],
        )
        assert result.exit_code == 0
        assert "0 files in 1 directories" in result.stdout
