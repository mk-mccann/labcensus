import dataclasses

import pytest

from labcensus.types import DirListing, FileStat


def make_stat(path: str, **overrides) -> FileStat:
    defaults = {
        "path": path,
        "size": 1024,
        "mtime": 1_700_000_000.0,
        "uid": 501,
        "gid": 20,
        "ino": 1,
        "dev": 1,
        "nlink": 1,
        "islink": False,
    }
    return FileStat(**{**defaults, **overrides})


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

    def test_membership_is_how_detectors_will_sniff(self):
        listing = DirListing.build(
            "/rec", (make_stat("/rec/settings.xml"),), {"continuous"}
        )
        assert "settings.xml" in listing.filenames
        assert "continuous" in listing.subdirs
