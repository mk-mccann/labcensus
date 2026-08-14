"""Detector rollups over a real index — wiring and aggregation, not detector accuracy.

These tests prove `classify_index()` correctly drives `iter_dir_listings`,
`sniff`, and `subtree_size`, and correctly aggregates their results —
including the multi-hit and zero-hit cases. They do **not** prove any
detector correctly recognises real-world neuroscience data: every fixture
below is a small, hand-built synthetic tree, sized so every assertion is
exact, hand-verifiable arithmetic. Detector accuracy is validated separately
against real downloaded datasets, outside this repo's automated tests.
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest

from labcensus.classify import classify_index
from labcensus.detectors import load_detectors
from labcensus.detectors.ephys_openephys import OpenEphysDetector
from labcensus.index import IndexWriter
from labcensus.index.summary import IncompleteIndexError, summarise
from labcensus.walker import walk

DETECTORS = load_detectors(include_plugins=False)


def _files(dirpath, *names, size=100):
    dirpath.mkdir(parents=True, exist_ok=True)
    for name in names:
        (dirpath / name).write_bytes(b"\x00" * size)


@pytest.fixture
def mixed_tree(tmp_path):
    """Five detector hits (one directory firing two detectors), eight
    directories with none — including one with real, sizeable content whose
    parent is what actually matched, and two genuinely empty directories.
    """
    root = tmp_path / "lab"

    # open-ephys HIGH: structure.oebin (100B) directly in rec1/, with the
    # real payload two levels down — the shape that makes subtree sizing
    # matter, not just immediate-file sizing.
    _files(root / "rec1", "structure.oebin")
    _files(
        root / "rec1" / "continuous" / "Rhythm_FPGA-100.0", "continuous.dat", size=5000
    )

    # spikeglx HIGH: a proper stem-matched .bin/.meta pair.
    _files(root / "run_g0", "run_g0_t0.imec0.ap.bin", "run_g0_t0.imec0.ap.meta")

    # spikeglx MEDIUM: a .bin/.meta pair with no recognised stem grammar.
    _files(root / "run_other", "capture.bin", "capture.meta")

    # suite2p-legacy HIGH: the five core arrays plus ops.npy (legacy fires at
    # MEDIUM without a generation marker — ops.npy is what earns HIGH here).
    _files(
        root / "imaging" / "suite2p" / "plane0",
        "F.npy",
        "Fneu.npy",
        "spks.npy",
        "stat.npy",
        "iscell.npy",
        "ops.npy",
    )

    # combo: open-ephys HIGH *and* suite2p-legacy HIGH in one directory.
    _files(
        root / "combo",
        "structure.oebin",
        "F.npy",
        "Fneu.npy",
        "spks.npy",
        "stat.npy",
        "iscell.npy",
        "ops.npy",
    )
    (root / "combo" / "continuous").mkdir()  # empty — no hit, no size

    # No match: real content, but nothing any detector recognises.
    _files(root / "random_notes", "notes.docx", "figure.png")

    # No match: genuinely empty.
    (root / "empty_dir").mkdir()

    return root


def index_of(tmp_path, root, name="index.db"):
    """Walk `root` into a fresh index and return an open connection to it."""
    db = tmp_path / name
    with IndexWriter(db, hostname="test-host", now=1_700_000_000.0) as writer:
        walk(root, writer)
    return sqlite3.connect(db)


class TestDetectorGrouping:
    def test_only_detectors_that_matched_at_least_one_directory_appear(
        self, tmp_path, mixed_tree
    ):
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        assert {row.detector for row in rollup.detectors} == {
            "open-ephys",
            "spikeglx",
            "suite2p-legacy",
        }

    def test_rows_are_ordered_by_total_size_descending(self, tmp_path, mixed_tree):
        """This order depends on subtree sizing: open-ephys's 5100+700=5800
        only outranks suite2p-legacy's 600+700=1300 because rec1's nested
        continuous.dat is counted. Immediate-only sizing (100+700=800) would
        have ordered suite2p-legacy first instead — a regression here would
        silently reorder this list, not just shrink a number."""
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        assert [row.detector for row in rollup.detectors] == [
            "open-ephys",
            "suite2p-legacy",
            "spikeglx",
        ]


class TestConfidenceBreakdown:
    def test_high_and_medium_hits_from_the_same_detector_are_counted_separately(
        self, tmp_path, mixed_tree
    ):
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        (spikeglx,) = [row for row in rollup.detectors if row.detector == "spikeglx"]
        assert (spikeglx.confidence.high, spikeglx.confidence.medium) == (1, 1)


class TestSubtreeSizing:
    def test_a_hit_is_sized_by_its_full_subtree_not_just_its_own_files(
        self, tmp_path, mixed_tree
    ):
        """The direct regression test for the fix this module exists to
        make: rec1 has only 100B of its own files, but its subtree
        (structure.oebin + the nested continuous.dat) is 5100B."""
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        (open_ephys,) = [
            row for row in rollup.detectors if row.detector == "open-ephys"
        ]
        assert open_ephys.total_size == 5800  # 5100 (rec1) + 700 (combo)
        assert open_ephys.total_size != 800  # what immediate-only sizing would give


class TestMultiHitDirectory:
    def test_a_directory_matched_by_two_detectors_is_credited_to_both_in_full(
        self, tmp_path, mixed_tree
    ):
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        by_detector = {row.detector: row for row in rollup.detectors}

        combo_path = str(mixed_tree / "combo")
        open_ephys_sample = next(
            s for s in by_detector["open-ephys"].samples if s.path == combo_path
        )
        suite2p_sample = next(
            s for s in by_detector["suite2p-legacy"].samples if s.path == combo_path
        )
        assert open_ephys_sample.size == suite2p_sample.size == 700

    def test_the_overall_matched_total_uses_immediate_size_and_counts_each_directory_once(
        self, tmp_path, mixed_tree
    ):
        """Not affected by subtree sizing at all — this total is
        immediate-only by design, a clean partition of the whole tree."""
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        assert rollup.size_with_hits == 1800


class TestZeroHitDirectories:
    def test_count_and_size_of_unrecognised_directories(self, tmp_path, mixed_tree):
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        # Includes rec1/continuous/Rhythm_FPGA-100.0's 5000B: real content,
        # but the detector fired on its *parent*, not on it directly.
        assert (rollup.n_dirs_without_hits, rollup.size_without_hits) == (8, 5200)

    def test_empty_directories_count_as_zero_hits_too(self, tmp_path, mixed_tree):
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        unrecognised_paths = {row.path for row in rollup.sample_unrecognised}
        # top_n defaults to 10 and there are only 8 unrecognised dirs, so
        # every one of them is present, not just the largest few.
        assert str(mixed_tree / "empty_dir") in unrecognised_paths
        assert str(mixed_tree / "combo" / "continuous") in unrecognised_paths


class TestTotals:
    def test_total_size_equals_matched_plus_unrecognised(self, tmp_path, mixed_tree):
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        assert (
            rollup.total_size
            == rollup.size_with_hits + rollup.size_without_hits
            == 7000
        )

    def test_dir_count_equals_matched_plus_unrecognised(self, tmp_path, mixed_tree):
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        assert (
            rollup.n_dirs == rollup.n_dirs_with_hits + rollup.n_dirs_without_hits == 13
        )


class TestCrossCheckWithSummarise:
    def test_total_size_matches_summarise_for_the_same_index(
        self, tmp_path, mixed_tree
    ):
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        assert rollup.total_size == summarise(con).total_size

    def test_dir_count_matches_summarise_for_the_same_index(self, tmp_path, mixed_tree):
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        assert rollup.n_dirs == summarise(con).n_dirs


class TestSamples:
    def test_samples_per_detector_are_capped_at_top_n_but_counts_are_not(
        self, tmp_path, mixed_tree
    ):
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS, top_n=1)
        for row in rollup.detectors:
            assert len(row.samples) == 1
        (open_ephys,) = [
            row for row in rollup.detectors if row.detector == "open-ephys"
        ]
        assert open_ephys.n_dirs == 2

    def test_unrecognised_samples_are_sorted_largest_first_and_capped(
        self, tmp_path, mixed_tree
    ):
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS, top_n=1)
        assert len(rollup.sample_unrecognised) == 1
        # The 5000B nested-continuous directory beats the 200B random_notes
        # and every 0B empty directory.
        assert rollup.sample_unrecognised[0].size == 5000


class TestDetectorSelection:
    def test_a_detector_not_passed_in_never_produces_a_row(self, tmp_path, mixed_tree):
        con = index_of(tmp_path, mixed_tree)
        rollup = classify_index(con, detectors=(OpenEphysDetector(),))
        assert {row.detector for row in rollup.detectors} == {"open-ephys"}


class TestIndexOnlyOperation:
    def test_classify_reads_only_the_index_not_the_original_tree(
        self, tmp_path, mixed_tree
    ):
        con = index_of(tmp_path, mixed_tree)
        shutil.rmtree(mixed_tree)
        rollup = classify_index(con, detectors=DETECTORS)
        assert rollup.n_dirs == 13
        assert rollup.n_dirs_with_hits == 5


class TestIncompleteIndex:
    def test_an_index_with_no_finished_scan_says_so(self, tmp_path):
        db = tmp_path / "empty.db"
        with IndexWriter(db, hostname="h", now=1_700_000_000.0):
            pass
        with pytest.raises(IncompleteIndexError, match="interrupted"):
            classify_index(sqlite3.connect(db), detectors=DETECTORS)


class TestEmptyTree:
    def test_classifying_a_tree_with_no_recognisable_content_reports_zero_hits_not_a_crash(
        self, tmp_path
    ):
        root = tmp_path / "bare"
        root.mkdir()
        con = index_of(tmp_path, root)
        rollup = classify_index(con, detectors=DETECTORS)
        assert rollup.n_dirs_with_hits == 0
        assert rollup.n_dirs_without_hits == rollup.n_dirs
