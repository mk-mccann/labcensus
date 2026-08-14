"""The CLI surface, including the guardrails that protect the read-only claim."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from labcensus import cli as cli_module
from labcensus.cli import app
from labcensus.index import IndexWriter

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


def classify(db_path, *extra):
    return runner.invoke(app, ["classify", str(db_path), *extra])


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


class TestClassifyCommand:
    def test_classify_is_a_subcommand(self):
        result = runner.invoke(app, ["classify", "--help"])
        assert result.exit_code == 0
        assert "Detect known data formats" in result.stdout

    def test_reports_a_detected_format(self, tree, tmp_path):
        scan(tree, tmp_path)
        result = classify(tmp_path / "out.db")
        assert result.exit_code == 0
        assert "open-ephys" in result.stdout

    def test_reports_the_unrecognised_total(self, tree, tmp_path):
        scan(tree, tmp_path)
        result = classify(tmp_path / "out.db")
        assert "Unrecognised:" in result.stdout

    def test_top_bounds_the_sample_rows(self, tree, tmp_path):
        scan(tree, tmp_path)
        result = classify(tmp_path / "out.db", "--top", "1")
        assert result.exit_code == 0


class TestClassifyReadOnly:
    def test_the_index_file_is_not_modified(self, tree, tmp_path):
        scan(tree, tmp_path)
        db_path = tmp_path / "out.db"
        before = (db_path.stat().st_mtime_ns, db_path.stat().st_size)
        classify(db_path)
        after = (db_path.stat().st_mtime_ns, db_path.stat().st_size)
        assert before == after, "classify modified the index it was reading"


class TestClassifyFailureModes:
    def test_missing_index_exits_cleanly_and_creates_nothing(self, tmp_path):
        target = tmp_path / "nope.db"
        result = classify(target)
        assert result.exit_code == 2
        assert "not a file" in result.output
        assert not target.exists()

    def test_a_directory_is_not_a_file(self, tmp_path):
        result = classify(tmp_path)
        assert result.exit_code == 2
        assert "not a file" in result.output

    def test_a_non_sqlite_file_is_rejected_cleanly(self, tmp_path):
        target = tmp_path / "not_an_index.db"
        target.write_text("this is not a sqlite file")
        result = classify(target)
        assert result.exit_code == 2
        assert "not a valid index" in result.output

    def test_an_unfinished_scan_is_rejected_cleanly(self, tmp_path):
        db_path = tmp_path / "unfinished.db"
        with IndexWriter(db_path, hostname="h", now=1_700_000_000.0):
            pass
        result = classify(db_path)
        assert result.exit_code == 2
        assert "interrupted" in result.output


class TestPluginDefault:
    def test_plugins_are_off_by_default(self, tree, tmp_path, monkeypatch):
        seen = []

        def spy(*, include_plugins):
            seen.append(include_plugins)
            return ()

        monkeypatch.setattr(cli_module, "load_detectors", spy)
        scan(tree, tmp_path)
        classify(tmp_path / "out.db")
        assert seen == [False]

    def test_plugins_flag_enables_plugin_loading(self, tree, tmp_path, monkeypatch):
        seen = []

        def spy(*, include_plugins):
            seen.append(include_plugins)
            return ()

        monkeypatch.setattr(cli_module, "load_detectors", spy)
        scan(tree, tmp_path)
        classify(tmp_path / "out.db", "--plugins")
        assert seen == [True]
