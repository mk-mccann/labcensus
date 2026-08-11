import dataclasses

import pytest

from labcensus.types import (
    DirListing,
    FileStat,
    Owner,
    OwnerKind,
    posix_owner,
    windows_owner,
)


def make_stat(path: str, **overrides) -> FileStat:
    defaults = {
        "path": path,
        "size": 1024,
        "mtime": 1_700_000_000.0,
        "owner": posix_owner(501),
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
        listing = DirListing.build("/rec", files, {"continuous", "events"})

        assert listing.filenames == {"settings.xml", "notes.txt"}
        assert listing.subdirs == {"continuous", "events"}
        assert listing.name == "rec"

    def test_build_accepts_empty_directory(self):
        listing = DirListing.build("/empty", (), set())
        assert listing.files == ()
        assert listing.filenames == frozenset()

    @pytest.mark.parametrize("probe", ["settings.xml", "Settings.xml", "SETTINGS.XML"])
    def test_has_file_is_case_insensitive(self, probe):
        listing = DirListing.build("/rec", (make_stat("/rec/Settings.XML"),), set())
        assert listing.has_file(probe)

    @pytest.mark.parametrize("probe", ["continuous", "Continuous", "CONTINUOUS"])
    def test_has_subdir_is_case_insensitive(self, probe):
        listing = DirListing.build("/rec", (), {"Continuous"})
        assert listing.has_subdir(probe)

    def test_original_casing_is_preserved_for_display(self):
        listing = DirListing.build("/rec", (make_stat("/rec/Settings.XML"),), {"Cont"})
        assert listing.filenames == {"Settings.XML"}
        assert listing.subdirs == {"Cont"}

    def test_absent_names_report_absent(self):
        listing = DirListing.build("/rec", (make_stat("/rec/a.tif"),), {"plane0"})
        assert not listing.has_file("settings.xml")
        assert not listing.has_subdir("continuous")
