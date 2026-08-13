"""Rollups, and the failure modes an interrupted scan produces."""

from __future__ import annotations

import os
import sqlite3
import time

import pytest

from labcensus.index import IndexWriter
from labcensus.index.summary import IncompleteIndexError, human_size, summarise
from labcensus.index.writer import ScanAlreadyRecordedError
from labcensus.walker import walk

NOW = 1_700_000_000.0
DAY = 86400.0


@pytest.fixture
def aged_tree(tmp_path):
    """Files spanning every age band, plus one dated in the future."""
    root = tmp_path / "lab"
    root.mkdir()
    for name, age_days in (
        ("yesterday.dat", 1),
        ("six_months.dat", 180),
        ("two_years.dat", 730),
        ("ancient.dat", 2000),
    ):
        target = root / name
        target.write_bytes(b"\x00" * 100)
        stamp = NOW - age_days * DAY
        os.utime(target, (stamp, stamp))

    # Clock skew and restored timestamps both produce these on real storage.
    future = root / "future.dat"
    future.write_bytes(b"\x00" * 100)
    os.utime(future, (NOW + 3 * DAY, NOW + 3 * DAY))
    return root


def index_of(tmp_path, root):
    db = tmp_path / "index.db"
    with IndexWriter(db, hostname="h", now=NOW) as writer:
        walk(root, writer)
    return sqlite3.connect(db)


class TestAgeBands:
    def test_every_file_lands_in_exactly_one_band(self, tmp_path, aged_tree):
        summary = summarise(index_of(tmp_path, aged_tree), now=NOW)
        assert sum(row.count for row in summary.ages) == summary.n_files == 5

    def test_future_timestamps_are_not_dropped(self, tmp_path, aged_tree):
        summary = summarise(index_of(tmp_path, aged_tree), now=NOW)
        newest = summary.ages[0]
        # yesterday.dat and future.dat both belong to the newest band.
        assert newest.count == 2

    def test_bands_are_labelled_as_the_ranges_they_query(self, tmp_path, aged_tree):
        summary = summarise(index_of(tmp_path, aged_tree), now=NOW)
        by_label = {row.label: row.count for row in summary.ages}
        assert by_label["< 1 month"] == 2
        assert by_label["1 month – 1 year"] == 1
        assert by_label["1 – 3 years"] == 1
        assert by_label["> 3 years"] == 1


class TestTotals:
    def test_sizes_and_counts(self, tmp_path, aged_tree):
        summary = summarise(index_of(tmp_path, aged_tree), now=NOW)
        assert summary.n_files == 5
        assert summary.total_size == 500
        assert summary.root == str(aged_tree)

    def test_suffixes_are_grouped(self, tmp_path, aged_tree):
        summary = summarise(index_of(tmp_path, aged_tree), now=NOW)
        assert [(row.suffix, row.count) for row in summary.top_suffixes] == [
            (".dat", 5)
        ]

    def test_largest_directories_carry_reconstructed_paths(self, tmp_path, aged_tree):
        summary = summarise(index_of(tmp_path, aged_tree), now=NOW)
        assert summary.largest_dirs[0].path == "lab"
        assert summary.largest_dirs[0].count == 5


def _fail_mid_scan(db, root=None):
    """Open an index, optionally walk, then fail — as an interrupt would."""
    with IndexWriter(db, hostname="h", now=NOW) as writer:
        if root is not None:
            walk(root, writer)
        raise RuntimeError("interrupted")


class TestIncompleteIndex:
    def test_an_interrupted_scan_leaves_nothing_behind(self, tmp_path, aged_tree):
        db = tmp_path / "index.db"
        with pytest.raises(RuntimeError):
            _fail_mid_scan(db, aged_tree)
        # Nothing to trip over on the next attempt.
        assert not db.exists()

    def test_a_preexisting_index_is_not_deleted_on_failure(self, tmp_path):
        # Only a file this run created is cleaned up; anything already there
        # belongs to someone else.
        db = tmp_path / "index.db"
        with IndexWriter(db, hostname="h", now=NOW):
            pass
        assert db.exists()

        with pytest.raises(RuntimeError):
            _fail_mid_scan(db)
        assert db.exists()

    def test_an_index_with_no_finished_scan_says_so(self, tmp_path):
        db = tmp_path / "empty.db"
        with IndexWriter(db, hostname="h", now=NOW):
            pass
        with pytest.raises(IncompleteIndexError, match="interrupted"):
            summarise(sqlite3.connect(db), now=NOW)


class TestOneScanPerIndex:
    def test_a_second_scan_into_one_index_is_refused(self, tmp_path, aged_tree):
        db = tmp_path / "index.db"
        with IndexWriter(db, hostname="h", now=NOW) as writer:
            walk(aged_tree, writer)
            with pytest.raises(ScanAlreadyRecordedError):
                walk(aged_tree, writer)


class TestHumanSize:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, "—"),
            (0, "0 B"),
            (999, "999 B"),
            (1024, "1.0 KB"),
            (1024**3, "1.0 GB"),
            (5 * 1024**4, "5.0 TB"),
        ],
    )
    def test_formats(self, value, expected):
        assert human_size(value) == expected


def test_summarise_defaults_now_to_the_clock(tmp_path, aged_tree):
    # The band boundaries are relative to now; the default must be usable.
    summary = summarise(index_of(tmp_path, aged_tree))
    assert sum(row.count for row in summary.ages) == 5
    assert summary.ages[0].label == "< 1 month"
    assert time.time() > NOW
