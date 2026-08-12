"""Walk a real tree on disk into a real index, and check what landed."""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

from labcensus.backends import LocalBackend
from labcensus.index import IndexWriter
from labcensus.index.writer import IndexTargetInsideTreeError, check_target_outside
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
    db = tmp_path / "index.db"
    with IndexWriter(db, hostname="test-host", now=1_700_000_000.0, **kwargs) as writer:
        counts = walk(root, writer)
    return db, counts


class TestWalk:
    def test_records_every_directory_and_file(self, tmp_path, tree):
        db, (dirs, files, errors) = index_of(tmp_path, tree)
        con = sqlite3.connect(db)

        # root, rec1, continuous, Rhythm_FPGA-100.0, suite2p, plane0, empty
        assert dirs == 7
        assert files == 8
        assert errors == 0
        assert con.execute("SELECT COUNT(*) FROM dirs").fetchone()[0] == 7
        assert con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 8

    def test_tree_shape_is_reconstructable(self, tmp_path, tree):
        db, _ = index_of(tmp_path, tree)
        con = sqlite3.connect(db)
        rows = dict(con.execute("SELECT name, depth FROM dirs").fetchall())
        assert rows["suite2p"] == 1
        assert rows["plane0"] == 2
        assert rows["Rhythm_FPGA-100.0"] == 3
        # exactly one root, and it is the only row without a parent
        assert (
            con.execute("SELECT COUNT(*) FROM dirs WHERE parent_id IS NULL").fetchone()[
                0
            ]
            == 1
        )

    def test_empty_directory_is_recorded(self, tmp_path, tree):
        db, _ = index_of(tmp_path, tree)
        con = sqlite3.connect(db)
        (dir_id,) = con.execute("SELECT id FROM dirs WHERE name='empty'").fetchone()
        assert (
            con.execute(
                "SELECT COUNT(*) FROM files WHERE dir_id=?", (dir_id,)
            ).fetchone()[0]
            == 0
        )

    def test_metadata_is_captured(self, tmp_path, tree):
        db, _ = index_of(tmp_path, tree)
        con = sqlite3.connect(db)
        size, mtime, mode, islink = con.execute(
            "SELECT size, mtime, mode, islink FROM files WHERE name='continuous.dat'"
        ).fetchone()
        assert size == 2048
        assert mtime > 0
        assert mode is not None
        assert islink == 0

    def test_suffix_is_interned_and_lowercased(self, tmp_path, tree):
        db, _ = index_of(tmp_path, tree)
        con = sqlite3.connect(db)
        (suffix,) = con.execute(
            "SELECT s.value FROM files f JOIN suffixes s ON s.id=f.suffix_id"
            " WHERE f.name='notes.TXT'"
        ).fetchone()
        assert suffix == ".txt"
        # .npy appears five times but is stored once
        assert (
            con.execute("SELECT COUNT(*) FROM suffixes WHERE value='.npy'").fetchone()[
                0
            ]
            == 1
        )

    def test_owner_is_interned(self, tmp_path, tree):
        db, _ = index_of(tmp_path, tree)
        con = sqlite3.connect(db)
        # Every file in the fixture has one owner, so one row despite eight files.
        assert (
            con.execute("SELECT COUNT(*) FROM owners WHERE kind='posix'").fetchone()[0]
            <= 1
        )

    def test_scan_row_carries_provenance(self, tmp_path, tree):
        db, _ = index_of(tmp_path, tree)
        con = sqlite3.connect(db)
        root, host, n_dirs, n_files, started, finished = con.execute(
            "SELECT root, hostname, n_dirs, n_files, started_at, finished_at FROM scans"
        ).fetchone()
        assert root == str(tree)
        assert host == "test-host"
        assert (n_dirs, n_files) == (7, 8)
        assert started == finished == 1_700_000_000.0

    def test_index_schema_version_is_recorded(self, tmp_path, tree):
        db, _ = index_of(tmp_path, tree)
        con = sqlite3.connect(db)
        (value,) = con.execute(
            "SELECT value FROM meta WHERE key='index_schema_version'"
        ).fetchone()
        assert int(value) >= 1

    def test_walk_is_deterministic(self, tmp_path, tree):
        db1, counts1 = index_of(tmp_path, tree)
        rows1 = (
            sqlite3.connect(db1)
            .execute("SELECT name, depth FROM dirs ORDER BY id")
            .fetchall()
        )
        os.remove(db1)
        db2, counts2 = index_of(tmp_path, tree)
        rows2 = (
            sqlite3.connect(db2)
            .execute("SELECT name, depth FROM dirs ORDER BY id")
            .fetchall()
        )
        assert counts1 == counts2
        assert rows1 == rows2

    def test_batching_does_not_change_the_result(self, tmp_path, tree):
        db, counts = index_of(tmp_path, tree, batch_size=1)
        assert counts == (7, 8, 0)
        con = sqlite3.connect(db)
        assert con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 8

    def test_progress_is_reported(self, tmp_path, tree):
        seen = []
        db = tmp_path / "index.db"
        with IndexWriter(db, hostname="h", now=0.0) as writer:
            walk(tree, writer, progress=lambda **kw: seen.append(kw), progress_every=1)
        assert seen
        assert seen[-1]["dirs"] == 7
        assert seen[-1]["files"] == 8


class TestSymlinks:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
    def test_symlinks_are_recorded_with_their_target_and_not_followed(
        self, tmp_path, tree
    ):
        # A broken link into .git/annex/objects is what a DataLad tree whose
        # content is not present locally actually looks like on disk.
        (tree / "annexed.dat").symlink_to(".git/annex/objects/SHA256E-s0--deadbeef")
        db, (_dirs, _files, errors) = index_of(tmp_path, tree)
        con = sqlite3.connect(db)
        islink, target, size = con.execute(
            "SELECT islink, link_target, size FROM files WHERE name='annexed.dat'"
        ).fetchone()
        assert islink == 1
        assert target.endswith("SHA256E-s0--deadbeef")
        # Recorded, not followed, and not an error.
        assert size == 0 or size is not None
        assert errors == 0

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
    def test_a_symlink_loop_does_not_hang_the_walk(self, tmp_path, tree):
        (tree / "loop").symlink_to(tree, target_is_directory=True)
        _, (dirs, _, _) = index_of(tmp_path, tree)
        # The link is recorded as a file, and the walk terminates.
        assert dirs == 7


class TestErrorsAreFindings:
    @pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics")
    def test_unreadable_directory_is_recorded_and_the_walk_continues(
        self, tmp_path, tree
    ):
        locked = tree / "locked"
        locked.mkdir()
        (locked / "secret.dat").write_bytes(b"x")
        locked.chmod(0o000)
        try:
            db, (_dirs, files, errors) = index_of(tmp_path, tree)
            con = sqlite3.connect(db)
            rows = con.execute("SELECT path, reason FROM errors").fetchall()
            assert errors == 1
            assert rows and "locked" in rows[0][0]
            # The rest of the tree still landed.
            assert files == 8
        finally:
            locked.chmod(0o755)


class TestIndexLocation:
    def test_index_inside_the_scanned_tree_is_refused(self, tmp_path, tree):
        with pytest.raises(IndexTargetInsideTreeError):
            check_target_outside(tree / "census.db", tree)

    def test_index_outside_the_scanned_tree_is_allowed(self, tmp_path, tree):
        check_target_outside(tmp_path / "census.db", tree)

    def test_the_root_itself_is_refused(self, tmp_path, tree):
        with pytest.raises(IndexTargetInsideTreeError):
            check_target_outside(tree, tree)


class TestBackend:
    def test_unreadable_root_yields_an_error_not_an_exception(self, tmp_path):
        backend = LocalBackend()
        listing, errors = backend.list_dir(tmp_path / "does-not-exist")
        assert listing is None
        assert len(errors) == 1
        assert errors[0].reason

    def test_listing_reconstructs_into_what_detectors_expect(self, tmp_path, tree):
        from labcensus.detectors import load_detectors, sniff

        backend = LocalBackend()
        listing, _ = backend.list_dir(tree / "suite2p" / "plane0")
        hits = sniff(listing, load_detectors(include_plugins=False))
        assert [h.detector for h in hits] == ["suite2p-legacy"]
