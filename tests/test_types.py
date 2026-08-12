import dataclasses
from pathlib import PurePath, PurePosixPath, PureWindowsPath

import pytest

from labcensus.types import (
    DirListing,
    FileStat,
    Owner,
    OwnerKind,
    posix_group,
    posix_owner,
    windows_owner,
)


def make_stat(path: str | PurePath, **overrides) -> FileStat:
    """A FileStat for `path`.

    A plain string is taken as POSIX, which is what most of these tests use.
    Cases that care about Windows pass a `PureWindowsPath`, exactly as a
    Windows backend would.
    """
    defaults = {
        "path": PurePosixPath(path) if isinstance(path, str) else path,
        "size": 1024,
        "blocks": 8,
        "mtime": 1_700_000_000.0,
        "btime": None,
        "atime": 1_700_000_000.0,
        "owner": posix_owner(501),
        "group": posix_group(20),
        "mode": 0o100644,
        "ino": 1,
        "dev": 1,
        "nlink": 1,
        "islink": False,
    }
    return FileStat(**{**defaults, **overrides})


class TestOwner:
    def test_posix_owner_is_interned(self):
        # A tree has millions of files but a handful of owners.
        assert posix_owner(1417) is posix_owner(1417)

    def test_windows_owner_is_interned(self):
        sid = "S-1-5-21-1004336348-1177238915-682003330-512"
        assert windows_owner(sid) is windows_owner(sid)

    def test_kinds_do_not_collide(self):
        assert posix_owner(512) != windows_owner("512")

    def test_identifier_kept_raw(self):
        # Resolution is the orphan heuristic's job, not this type's.
        assert posix_owner(1417) == Owner(OwnerKind.POSIX, "1417")


class TestFileStat:
    def test_is_frozen(self):
        stat = make_stat("/data/a.tif")
        with pytest.raises(dataclasses.FrozenInstanceError):
            stat.size = 2048

    def test_has_no_dict(self):
        # slots=True: millions of these exist during a scan.
        assert not hasattr(make_stat("/data/a.tif"), "__dict__")

    def test_name(self):
        assert make_stat("/data/rec/settings.xml").name == "settings.xml"

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/data/a.tif", ".tif"),
            ("/data/a.TIF", ".tif"),
            ("/data/a.Ome.Tiff", ".tiff"),
            ("/data/archive.tar.gz", ".gz"),
            ("/data/README", ""),
            ("/data/.gitignore", ""),
            ("/data/dotted.dir/file", ""),
        ],
    )
    def test_suffix(self, path, expected):
        assert make_stat(path).suffix == expected

    def test_owner_may_be_absent(self):
        # An S3 backend, or a permission-denied stat, has no owner to report.
        assert make_stat("/data/a.tif", owner=None).owner is None


class TestPathFlavour:
    """The backend pins the flavour; every platform then parses correctly.

    Each case here returned nonsense when `name`/`suffix` were derived by
    splitting the path with `posixpath`, and returned it *silently* — which on
    a Windows rig means a scan that finds nothing and reports no error.
    """

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            (PureWindowsPath(r"C:\lab\rec\settings.xml"), "settings.xml"),
            (PureWindowsPath(r"\\server\share\rec\continuous.dat"), "continuous.dat"),
            (PureWindowsPath(r"C:\lab\data.v2\README"), "README"),
            (PureWindowsPath(r"D:\Miniscope\0.avi"), "0.avi"),
            (PureWindowsPath("C:/lab/forward/slashes.tif"), "slashes.tif"),
            (PurePosixPath("/mnt/nas/rec/settings.xml"), "settings.xml"),
        ],
    )
    def test_name(self, path, expected):
        assert make_stat(path).name == expected

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            (PureWindowsPath(r"C:\lab\rec\settings.xml"), ".xml"),
            (PureWindowsPath(r"C:\lab\data.v2\README"), ""),
            (PureWindowsPath(r"C:\lab\scan_00001.TIF"), ".tif"),
            (PureWindowsPath(r"\\server\share\run_g0_t0.imec0.ap.bin"), ".bin"),
        ],
    )
    def test_suffix(self, path, expected):
        assert make_stat(path).suffix == expected

    def test_posix_name_may_contain_a_backslash(self):
        # Legal on POSIX, and the reason a Windows flavour is not a universal
        # answer either: it would report this file as "name.tif".
        stat = make_stat(PurePosixPath(r"/data/weird\name.tif"))
        assert stat.name == r"weird\name.tif"
        assert stat.suffix == ".tif"

    def test_str_round_trips_to_the_native_form(self):
        # The report shows a PI a path they can paste into their own file
        # browser, so serialization must not normalise separators.
        win = r"C:\lab\rec\settings.xml"
        assert str(make_stat(PureWindowsPath(win)).path) == win

    def test_stem_pairing_survives(self):
        # SpikeGLX is a same-stem .bin/.meta pairing — the detector needs this.
        bin_ = make_stat(PureWindowsPath(r"C:\lab\run_g0_t0.imec0.ap.bin"))
        assert bin_.path.with_suffix(".meta").name == "run_g0_t0.imec0.ap.meta"

    def test_windows_detector_probe_hits(self):
        # The regression this class exists for: `settings.xml` is exactly the
        # probe that identifies an Open-Ephys recording.
        listing = DirListing.build(
            PureWindowsPath(r"C:\lab\rec"),
            (make_stat(PureWindowsPath(r"C:\lab\rec\settings.xml")),),
            {"continuous"},
        )
        assert listing.has_file("settings.xml")
        assert listing.has_subdir("continuous")
        assert listing.name == "rec"


class TestHardlinkKey:
    def test_single_link_is_not_deduplicable(self):
        assert make_stat("/data/a.tif", nlink=1).hardlink_key is None

    def test_multiple_links_key_on_dev_and_ino(self):
        stat = make_stat("/data/a.tif", nlink=2, dev=66, ino=1234)
        assert stat.hardlink_key == (66, 1234)

    @pytest.mark.parametrize("missing", ["ino", "dev", "nlink"])
    def test_missing_field_means_count_it(self, missing):
        # Windows may supply none of these. Returning None makes the caller
        # count the file rather than silently drop it as a duplicate.
        stat = make_stat("/data/a.tif", **{"nlink": 2, missing: None})
        assert stat.hardlink_key is None

    def test_two_names_for_one_file_share_a_key(self):
        a = make_stat("/data/a.tif", nlink=2, dev=66, ino=1234)
        b = make_stat("/data/b.tif", nlink=2, dev=66, ino=1234)
        assert a.hardlink_key == b.hardlink_key


class TestDirListing:
    def test_build_derives_membership_sets(self):
        files = (make_stat("/rec/settings.xml"), make_stat("/rec/notes.txt"))
        listing = DirListing.build(
            PurePosixPath("/rec"), files, {"continuous", "events"}
        )

        assert listing.filenames == {"settings.xml", "notes.txt"}
        assert listing.subdirs == {"continuous", "events"}
        assert listing.name == "rec"

    def test_build_accepts_empty_directory(self):
        listing = DirListing.build(PurePosixPath("/empty"), (), set())
        assert listing.files == ()
        assert listing.filenames == frozenset()

    @pytest.mark.parametrize("probe", ["settings.xml", "Settings.xml", "SETTINGS.XML"])
    def test_has_file_is_case_insensitive(self, probe):
        listing = DirListing.build(
            PurePosixPath("/rec"), (make_stat("/rec/Settings.XML"),), set()
        )
        assert listing.has_file(probe)

    @pytest.mark.parametrize("probe", ["continuous", "Continuous", "CONTINUOUS"])
    def test_has_subdir_is_case_insensitive(self, probe):
        listing = DirListing.build(PurePosixPath("/rec"), (), {"Continuous"})
        assert listing.has_subdir(probe)

    def test_original_casing_is_preserved_for_display(self):
        listing = DirListing.build(
            PurePosixPath("/rec"), (make_stat("/rec/Settings.XML"),), {"Cont"}
        )
        assert listing.filenames == {"Settings.XML"}
        assert listing.subdirs == {"Cont"}

    def test_absent_names_report_absent(self):
        listing = DirListing.build(
            PurePosixPath("/rec"), (make_stat("/rec/a.tif"),), {"plane0"}
        )
        assert not listing.has_file("settings.xml")
        assert not listing.has_subdir("continuous")
