"""Detector tests.

Detectors consume `DirListing`, which is stable, so they can be written and
tested well before a walker or backend exists — listings are built directly
here rather than from real trees.

The negative cases matter more than the positive ones. A detector that misses a
recording costs a finding; one that confidently mislabels a directory costs the
credibility the whole report depends on.
"""

from pathlib import PurePosixPath, PureWindowsPath

import pytest

from labcensus.detectors import Confidence, load_detectors, sniff
from labcensus.detectors.behavior_deeplabcut import DeepLabCutDetector
from labcensus.detectors.caimg_caiman import CaimanDetector
from labcensus.detectors.caimg_suite2p import Suite2pDetector, Suite2pLegacyDetector
from labcensus.detectors.ephys_openephys import OpenEphysDetector
from labcensus.detectors.ephys_spikeglx import SpikeGLXDetector
from labcensus.types import DirListing, FileStat, posix_owner

SUITE2P_REQUIRED = ("F.npy", "Fneu.npy", "spks.npy", "stat.npy", "iscell.npy")
SUITE2P_CURRENT = ("reg_outputs.npy", "detect_outputs.npy")


def listing(path, filenames=(), subdirs=()):
    """A DirListing for `path` containing `filenames` and `subdirs`."""
    base = PurePosixPath(path) if isinstance(path, str) else path
    files = tuple(
        FileStat(
            path=base / name,
            size=1024,
            mtime=1_700_000_000.0,
            owner=posix_owner(501),
            ino=None,
            dev=None,
            nlink=None,
            islink=False,
        )
        for name in filenames
    )
    return DirListing.build(base, files, set(subdirs))


class TestOpenEphys:
    def test_binary_fires_on_sentinel(self):
        hit = OpenEphysDetector().sniff_dir(
            listing(
                "/nas/rec/Record Node 101/experiment1/recording1",
                ["structure.oebin", "sync_messages.txt"],
                ["continuous", "events"],
            )
        )
        assert hit is not None
        assert hit.variant == "binary"
        assert hit.confidence is Confidence.HIGH
        assert "structure.oebin" in hit.evidence
        assert "continuous/" in hit.evidence

    def test_legacy_fires_on_continuous_files(self):
        hit = OpenEphysDetector().sniff_dir(
            listing(
                "/nas/rec/2019-04-02_15-11-03",
                [
                    "100_CH1.continuous",
                    "100_CH2.continuous",
                    "all_channels.events",
                    "Continuous_Data.openephys",
                    "settings.xml",
                ],
            )
        )
        assert hit is not None
        assert hit.variant == "legacy"
        assert hit.confidence is Confidence.HIGH
        assert "*.continuous ×2" in hit.evidence
        assert "settings.xml" in hit.evidence

    def test_binary_wins_when_both_shapes_are_present(self):
        # A converted tree can hold both. The sentinel is the stronger signal.
        hit = OpenEphysDetector().sniff_dir(
            listing(
                "/nas/rec", ["structure.oebin", "100_CH1.continuous"], ["continuous"]
            )
        )
        assert hit.variant == "binary"

    def test_bare_dat_is_not_open_ephys(self):
        # Six neo readers claim `.dat`. Upstream tools fall through to it only
        # because a human already named the format; as a classifier that would
        # claim a large share of a lab NAS.
        assert (
            OpenEphysDetector().sniff_dir(
                listing("/nas/misc", ["continuous.dat", "notes.txt"])
            )
            is None
        )

    def test_empty_directory_is_not_a_hit(self):
        assert OpenEphysDetector().sniff_dir(listing("/nas/empty")) is None

    def test_fires_on_a_windows_path(self):
        hit = OpenEphysDetector().sniff_dir(
            listing(
                PureWindowsPath(r"D:\Data\Record Node 101\experiment1\recording1"),
                ["structure.oebin"],
                ["continuous"],
            )
        )
        assert hit is not None and hit.variant == "binary"


class TestSpikeGLX:
    def test_fires_on_paired_stem_with_grammar(self):
        hit = SpikeGLXDetector().sniff_dir(
            listing(
                "/nas/run_g0/run_g0_imec0",
                [
                    "run_g0_t0.imec0.ap.bin",
                    "run_g0_t0.imec0.ap.meta",
                    "run_g0_t0.imec0.lf.bin",
                    "run_g0_t0.imec0.lf.meta",
                ],
            )
        )
        assert hit is not None
        assert hit.confidence is Confidence.HIGH
        assert "streams: ap, lf" in hit.evidence

    @pytest.mark.parametrize(
        "stem",
        [
            "run_g0_t0.nidq",
            "run_g0_tcat.imec0.ap",  # CatGT-concatenated
            "run_g12_t3.obx0.obx",
        ],
    )
    def test_grammar_variants(self, stem):
        hit = SpikeGLXDetector().sniff_dir(
            listing("/nas/run_g0", [f"{stem}.bin", f"{stem}.meta"])
        )
        assert hit is not None and hit.confidence is Confidence.HIGH

    def test_unpaired_bin_is_not_a_hit(self):
        assert (
            SpikeGLXDetector().sniff_dir(
                listing("/nas/run", ["run_g0_t0.imec0.ap.bin"])
            )
            is None
        )

    def test_pair_without_grammar_is_reduced_not_silent(self):
        # Still probably SpikeGLX with an unfamiliar naming scheme. Saying so at
        # reduced confidence beats staying quiet.
        hit = SpikeGLXDetector().sniff_dir(
            listing("/nas/other", ["capture.bin", "capture.meta"])
        )
        assert hit is not None and hit.confidence is Confidence.MEDIUM

    def test_windows_stem_pairing(self):
        hit = SpikeGLXDetector().sniff_dir(
            listing(
                PureWindowsPath(r"C:\Data\run_g0"),
                ["run_g0_t0.imec0.ap.bin", "run_g0_t0.imec0.ap.meta"],
            )
        )
        assert hit is not None and hit.confidence is Confidence.HIGH


class TestSuite2p:
    """The two generations, and the guarantee that they never both fire."""

    def test_current_fires_on_documented_markers(self):
        hit = Suite2pDetector().sniff_dir(
            listing("/nas/proj/suite2p/plane0", [*SUITE2P_REQUIRED, *SUITE2P_CURRENT])
        )
        assert hit is not None
        assert hit.variant == "current"
        assert hit.confidence is Confidence.HIGH
        assert "plane0/" in hit.evidence
        assert "suite2p/" in hit.evidence

    def test_current_records_undocumented_corroborators(self):
        # settings.npy and db.npy are written by run_s2p.py but absent from the
        # documented output list, so they corroborate without being required.
        hit = Suite2pDetector().sniff_dir(
            listing(
                "/nas/proj/suite2p/plane0",
                [*SUITE2P_REQUIRED, *SUITE2P_CURRENT, "settings.npy", "db.npy"],
            )
        )
        assert "settings.npy" in hit.evidence and "db.npy" in hit.evidence

    def test_legacy_fires_on_ops(self):
        hit = Suite2pLegacyDetector().sniff_dir(
            listing("/nas/proj/suite2p/plane0", [*SUITE2P_REQUIRED, "ops.npy"])
        )
        assert hit is not None
        assert hit.variant == "legacy"
        assert hit.confidence is Confidence.HIGH
        assert "ops.npy" in hit.evidence

    def test_undated_output_is_reported_at_reduced_confidence(self):
        # A pruned or partially copied tree. The five core arrays together are
        # unmistakable, so this is reported rather than dropped.
        hit = Suite2pLegacyDetector().sniff_dir(
            listing("/nas/copied/plane0", [*SUITE2P_REQUIRED])
        )
        assert hit is not None
        assert hit.confidence is Confidence.MEDIUM
        assert any("cannot be dated" in e for e in hit.evidence)

    def test_generations_are_mutually_exclusive(self):
        # The property the split depends on: current markers suppress legacy,
        # so a tree carrying both can never produce two HIGH hits.
        both = listing(
            "/nas/proj/suite2p/plane0",
            [*SUITE2P_REQUIRED, *SUITE2P_CURRENT, "ops.npy"],
        )
        assert Suite2pDetector().sniff_dir(both) is not None
        assert Suite2pLegacyDetector().sniff_dir(both) is None

    @pytest.mark.parametrize(
        "detector",
        [Suite2pDetector(), Suite2pLegacyDetector()],
        ids=["current", "legacy"],
    )
    def test_partial_array_set_is_not_a_hit(self, detector):
        assert (
            detector.sniff_dir(
                listing("/nas/proj/suite2p/plane0", ["F.npy", "Fneu.npy"])
            )
            is None
        )

    @pytest.mark.parametrize(
        "detector",
        [Suite2pDetector(), Suite2pLegacyDetector()],
        ids=["current", "legacy"],
    )
    def test_a_directory_of_unrelated_npy_is_not_suite2p(self, detector):
        # phy, Kilosort and Open-Ephys binary all write .npy.
        assert (
            detector.sniff_dir(
                listing(
                    "/nas/sorted",
                    ["spike_times.npy", "spike_clusters.npy", "params.py"],
                )
            )
            is None
        )


class TestCaiman:
    def test_mmap_grammar_fires_high(self):
        hit = CaimanDetector().sniff_dir(
            listing(
                "/nas/proj/caiman",
                ["memmap__d1_512_d2_512_d3_1_order_C_frames_3000.mmap"],
            )
        )
        assert hit is not None
        assert hit.confidence is Confidence.HIGH
        assert hit.variant == "memory-mapped"

    def test_older_trailing_underscore_form_also_fires(self):
        hit = CaimanDetector().sniff_dir(
            listing(
                "/nas/proj/caiman",
                ["Yr_d1_170_d2_170_d3_1_order_F_frames_1000_.mmap"],
            )
        )
        assert hit is not None and hit.confidence is Confidence.HIGH

    def test_bare_hdf5_is_never_claimed_as_caiman(self):
        # .hdf5 collides with NWB, SLEAP, MaxWell, Biocam, MEArec and
        # DeepLabCut. Confirming CaImAn needs the `estimates` group, which means
        # opening the file — T4, which v1 does not do. Missing a results file is
        # the correct trade against mislabelling someone else's.
        assert (
            CaimanDetector().sniff_dir(
                listing("/nas/proj/caiman", ["analysis_results.hdf5"])
            )
            is None
        )

    def test_does_not_fire_on_deeplabcut_output(self):
        # The regression this rule exists for: DLC writes .h5 next to videos.
        assert (
            CaimanDetector().sniff_dir(
                listing(
                    "/nas/videos",
                    ["mouse1DLC_resnet50_reachMar1shuffle1_100000.h5"],
                )
            )
            is None
        )

    def test_unrelated_mmap_is_not_caiman(self):
        assert (
            CaimanDetector().sniff_dir(listing("/nas/misc", ["scratch.mmap"])) is None
        )


class TestDeepLabCut:
    def test_project_layout_fires(self):
        hit = DeepLabCutDetector().sniff_dir(
            listing(
                "/nas/proj/reach-mk-2024-03-01",
                ["config.yaml"],
                ["labeled-data", "training-datasets", "dlc-models", "videos"],
            )
        )
        assert hit is not None
        assert hit.variant == "project"
        assert hit.confidence is Confidence.HIGH

    def test_config_yaml_alone_is_not_a_project(self):
        # config.yaml is one of the most common filenames in existence.
        assert (
            DeepLabCutDetector().sniff_dir(
                listing("/nas/some_tool", ["config.yaml"], ["logs"])
            )
            is None
        )

    def test_analysis_output_grammar_fires(self):
        hit = DeepLabCutDetector().sniff_dir(
            listing(
                "/nas/videos",
                [
                    "mouse1DLC_resnet50_reachMar1shuffle1_100000.h5",
                    "mouse1DLC_resnet50_reachMar1shuffle1_100000.csv",
                ],
            )
        )
        assert hit is not None
        assert hit.variant == "analysis output"
        assert hit.confidence is Confidence.HIGH

    def test_legacy_deepcut_scorer_is_flagged(self):
        hit = DeepLabCutDetector().sniff_dir(
            listing(
                "/nas/videos", ["mouse1DeepCut_resnet50_reachMar1shuffle1_50000.h5"]
            )
        )
        assert hit is not None
        assert any("pre-DLC 2.1" in e for e in hit.evidence)

    def test_plain_h5_and_csv_are_not_deeplabcut(self):
        # DeepLabCut declares (".h5", ".csv") as its suffixes, which is useless
        # for detection — the grammar is doing all the work.
        assert (
            DeepLabCutDetector().sniff_dir(
                listing("/nas/misc", ["results.h5", "table.csv"])
            )
            is None
        )


class TestRegistryAndRanking:
    def test_builtins_load_without_plugins(self):
        detectors = load_detectors(include_plugins=False)
        assert {d.name for d in detectors} == {
            "open-ephys",
            "spikeglx",
            "suite2p",
            "suite2p-legacy",
            "caiman",
            "deeplabcut",
        }

    def test_unrecognised_directory_yields_no_hits(self):
        # Graceful ignorance: this is a census finding, not a failure.
        assert (
            sniff(
                listing("/nas/old_stuff", ["notes.docx", "figure.png"]),
                load_detectors(include_plugins=False),
            )
            == ()
        )

    def test_one_directory_may_produce_several_hits(self):
        # Raw plus derived in one folder is two true findings, not a collision.
        hits = sniff(
            listing(
                "/nas/rec",
                ["structure.oebin", *SUITE2P_REQUIRED, "ops.npy"],
                ["continuous"],
            ),
            load_detectors(include_plugins=False),
        )
        assert {h.detector for h in hits} == {"open-ephys", "suite2p-legacy"}

    def test_hits_are_ordered_strongest_first_and_deterministically(self):
        hits = sniff(
            listing("/nas/mixed", ["capture.bin", "capture.meta", "structure.oebin"]),
            load_detectors(include_plugins=False),
        )
        assert [h.detector for h in hits] == ["open-ephys", "spikeglx"]
        assert hits[0].confidence > hits[1].confidence

    def test_no_third_party_library_is_imported(self):
        # The read-only guarantee is easiest to keep when nothing capable of
        # opening a data file is present at all.
        import sys

        load_detectors(include_plugins=False)
        forbidden = {"h5py", "neo", "pynwb", "zarr", "tifffile", "numpy"}
        assert forbidden.isdisjoint(sys.modules)
