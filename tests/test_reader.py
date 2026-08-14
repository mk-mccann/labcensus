"""Reconstruct DirListings from a real index, and check they match a live walk."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import PureWindowsPath

import pytest

from labcensus.backends import LocalBackend
from labcensus.index import IndexWriter
from labcensus.index.reader import dir_ids_by_path, iter_dir_listings, subtree_size
from labcensus.index.summary import IncompleteIndexError, summarise
from labcensus.types import posix_group, posix_owner
from labcensus.walker import walk


@pytest.fixture
def tree(tmp_path):
    """A small tree with the shapes that matter, built on the real filesystem."""
    root = tmp_path / "lab"
    (root / "rec1" / "continuous" / "Rhythm_FPGA-100.0").mkdir(parents=True)
    (root / "rec1" / "structure.oebin").write_text("{}")
    (root / "rec1" / "continuous" / "Rhythm_FPGA-100.0" / "continuous.dat").write_bytes(
        b"\x00" * 2048
    )
    (root / "suite2p" / "plane0").mkdir(parents=True)
    for name in ("F.npy", "Fneu.npy", "spks.npy", "stat.npy", "iscell.npy"):
        (root / "suite2p" / "plane0" / name).write_bytes(b"\x00" * 16)
    (root / "empty").mkdir()
    (root / "notes.TXT").write_text("hello")
    return root


def index_of(tmp_path, root, **kwargs):
    """Walk `root` into a fresh index and return an open connection to it."""
    db = tmp_path / "index.db"
    with IndexWriter(db, hostname="test-host", now=1_700_000_000.0, **kwargs) as writer:
        walk(root, writer)
    return sqlite3.connect(db)


def by_path(listings, path):
    """The one reconstructed listing whose path stringifies to `path`.

    Compared as strings rather than `PurePath` equality, since a live root
    like `tmp_path` is a concrete `Path` and callers pass whichever is handy.
    """
    want = str(path)
    return next(listing for listing in listings if str(listing.path) == want)


def dir_id_for(dir_ids, path):
    """The id for the one directory whose path stringifies to `path`.

    Compared as strings for the same reason `by_path` is: a live root like
    `tmp_path` is a concrete `Path`, callers pass whichever is handy.
    """
    want = str(path)
    return next(dir_id for p, dir_id in dir_ids.items() if str(p) == want)


class TestDetectorParity:
    def test_index_reconstructed_listing_fires_the_same_detector_as_a_live_scan(
        self, tmp_path, tree
    ):
        """The whole point of this module: a detector fires identically
        whether it is handed a live listing or one rebuilt from the index."""
        from labcensus.detectors import load_detectors, sniff

        detectors = load_detectors(include_plugins=False)
        target = tree / "suite2p" / "plane0"

        live_listing, live_errors = LocalBackend().list_dir(target)
        assert not live_errors
        live_hits = sniff(live_listing, detectors)

        con = index_of(tmp_path, tree)
        reconstructed_listing = by_path(iter_dir_listings(con), target)
        reconstructed_hits = sniff(reconstructed_listing, detectors)

        assert [h.detector for h in reconstructed_hits] == ["suite2p-legacy"]
        assert reconstructed_hits == live_hits


class TestRoundTrip:
    def test_directory_tree_reconstructs_to_match_a_live_walk(self, tmp_path, tree):
        """Every directory's path, subdirs, and file names/sizes/mtimes/modes
        must match what listing the same tree live would produce."""
        backend = LocalBackend()
        con = index_of(tmp_path, tree)
        reconstructed = {str(L.path): L for L in iter_dir_listings(con)}

        seen = 0
        for dirpath, _dirnames, _filenames in os.walk(tree):
            live, live_errors = backend.list_dir(dirpath)
            assert not live_errors
            got = reconstructed[str(live.path)]
            seen += 1

            assert got.subdirs == live.subdirs
            assert got.filenames == live.filenames

            live_by_name = {f.name: f for f in live.files}
            for f in got.files:
                want = live_by_name[f.name]
                assert (f.size, f.mtime, f.mode, f.islink) == (
                    want.size,
                    want.mtime,
                    want.mode,
                    want.islink,
                )

        assert seen == len(reconstructed) == 7


class TestBareRootRegression:
    def test_a_scan_rooted_at_a_bare_posix_slash_is_not_double_separated(
        self, tmp_path, tree
    ):
        """The regression this guards: string-concatenating '/' + '/' + name
        produces '//etc', and PurePosixPath does not collapse a leading
        double slash. root='/' is mounted onto a real index after the fact,
        so this needs no actual root-filesystem access.
        """
        con = index_of(tmp_path, tree)
        con.execute("UPDATE scans SET root = ?, platform = ?", ("/", "Linux"))
        con.commit()

        paths = {str(listing.path) for listing in iter_dir_listings(con)}
        assert "/" in paths
        assert "/empty" in paths
        assert all("//" not in path for path in paths)


class TestPlatformFlavour:
    def test_the_recorded_platform_picks_the_flavour_not_the_host_os(
        self, tmp_path, tree
    ):
        """A reader may run on a different machine than the one that scanned;
        the flavour must come from `scans.platform`, not `sys.platform`."""
        con = index_of(tmp_path, tree)
        con.execute("UPDATE scans SET root = ?, platform = ?", (r"C:\lab", "Windows"))
        con.commit()

        listings = list(iter_dir_listings(con))
        root_listing = by_path(listings, PureWindowsPath(r"C:\lab"))
        child_listing = by_path(listings, PureWindowsPath(r"C:\lab\empty"))

        assert isinstance(root_listing.path, PureWindowsPath)
        assert isinstance(child_listing.path, PureWindowsPath)
        assert str(child_listing.path) == r"C:\lab\empty"


class TestOwnerIdentity:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX owner/group semantics")
    def test_owner_and_group_are_the_same_interned_object_as_a_live_stat(
        self, tmp_path, tree
    ):
        """`owner`/`group` are cached singletons; the reader must hand back
        the SAME object a live stat would, not just an equal one."""
        target = tree / "suite2p" / "plane0" / "F.npy"
        live_stat = os.stat(target)

        con = index_of(tmp_path, tree)
        listing = by_path(iter_dir_listings(con), tree / "suite2p" / "plane0")
        (reconstructed,) = [f for f in listing.files if f.name == "F.npy"]

        assert reconstructed.owner is posix_owner(live_stat.st_uid)
        assert reconstructed.group is posix_group(live_stat.st_gid)


class TestEmptyDirectories:
    def test_an_empty_directory_reconstructs_with_no_files_and_no_subdirs(
        self, tmp_path, tree
    ):
        con = index_of(tmp_path, tree)
        listing = by_path(iter_dir_listings(con), tree / "empty")
        assert listing.files == ()
        assert listing.subdirs == frozenset()
        assert listing.filenames == frozenset()


class TestSymlinks:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
    def test_a_symlink_reconstructs_with_its_target_and_islink_flag(
        self, tmp_path, tree
    ):
        # A broken link into .git/annex/objects, matching test_walker.py's
        # DataLad-shaped case.
        (tree / "annexed.dat").symlink_to(".git/annex/objects/SHA256E-s0--deadbeef")
        con = index_of(tmp_path, tree)
        listing = by_path(iter_dir_listings(con), tree)

        (link,) = [f for f in listing.files if f.name == "annexed.dat"]
        assert link.islink is True
        assert link.link_target.endswith("SHA256E-s0--deadbeef")


class TestIncompleteIndex:
    def test_an_index_with_no_finished_scan_says_so(self, tmp_path):
        db = tmp_path / "empty.db"
        with IndexWriter(db, hostname="h", now=1_700_000_000.0):
            pass
        with pytest.raises(IncompleteIndexError, match="interrupted"):
            # iter_dir_listings is a generator: nothing runs, and nothing
            # raises, until it is actually iterated.
            list(iter_dir_listings(sqlite3.connect(db)))


class TestStreamingOrder:
    def test_zero_file_directories_interspersed_with_populated_ones(
        self, tmp_path, tree
    ):
        """The merge between the dirs stream and the grouped files stream must
        not desync when a directory with no files sits between two that have
        files — the shape `tree` already produces in scan order."""
        con = index_of(tmp_path, tree)
        got = [(listing.name, len(listing.files)) for listing in iter_dir_listings(con)]
        assert got == [
            ("lab", 1),  # notes.TXT
            ("empty", 0),
            ("rec1", 1),  # structure.oebin
            ("continuous", 0),
            ("Rhythm_FPGA-100.0", 1),  # continuous.dat
            ("suite2p", 0),
            ("plane0", 5),  # the five suite2p core arrays
        ]

    def test_consecutive_and_trailing_empty_directories(self, tmp_path):
        """Two empty directories back to back, and an empty directory as the
        very last one visited — the cases most likely to desync a hand-rolled
        merge between two ordered streams."""
        root = tmp_path / "lab"
        (root / "a").mkdir(parents=True)
        (root / "a" / "populated.dat").write_bytes(b"x")
        (root / "b").mkdir()  # empty
        (root / "c").mkdir()  # empty, back to back with "b"
        (root / "d").mkdir()
        (root / "d" / "also_populated.dat").write_bytes(b"x")
        (root / "e").mkdir()  # empty, and the last directory visited

        con = index_of(tmp_path, root)
        got = [(listing.name, len(listing.files)) for listing in iter_dir_listings(con)]
        assert got == [
            ("lab", 0),
            ("a", 1),
            ("b", 0),
            ("c", 0),
            ("d", 1),
            ("e", 0),
        ]


class TestDirIdsByPath:
    def test_every_listing_path_has_a_matching_id(self, tmp_path, tree):
        con = index_of(tmp_path, tree)
        dir_ids = dir_ids_by_path(con)
        listings = list(iter_dir_listings(con))

        assert len(dir_ids) == len(listings)
        for listing in listings:
            assert isinstance(dir_id_for(dir_ids, listing.path), int)

        (empty_id,) = con.execute("SELECT id FROM dirs WHERE name = 'empty'").fetchone()
        assert dir_id_for(dir_ids, tree / "empty") == empty_id


class TestSubtreeSize:
    def test_includes_files_in_nested_subdirectories(self, tmp_path, tree):
        """`rec1` holds `structure.oebin` (2 bytes) directly, and
        `continuous.dat` (2048 bytes) two levels down — the same shape a real
        Open-Ephys binary recording has. Subtree size must include both."""
        con = index_of(tmp_path, tree)
        dir_ids = dir_ids_by_path(con)
        rec1_id = dir_id_for(dir_ids, tree / "rec1")

        assert subtree_size(con, rec1_id) == 2 + 2048

        immediate_only = sum(
            f.size for f in by_path(iter_dir_listings(con), tree / "rec1").files
        )
        assert immediate_only != subtree_size(con, rec1_id)

    def test_a_leaf_directory_equals_its_own_immediate_size(self, tmp_path, tree):
        con = index_of(tmp_path, tree)
        dir_ids = dir_ids_by_path(con)
        plane0_id = dir_id_for(dir_ids, tree / "suite2p" / "plane0")
        assert subtree_size(con, plane0_id) == 5 * 16

    def test_the_root_equals_the_whole_tree(self, tmp_path, tree):
        con = index_of(tmp_path, tree)
        dir_ids = dir_ids_by_path(con)
        root_id = dir_id_for(dir_ids, tree)
        assert subtree_size(con, root_id) == summarise(con).total_size
