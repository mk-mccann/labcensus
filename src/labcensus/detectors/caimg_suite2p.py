"""suite2p segmentation output, as one detector per output generation.

Both generations write the same five core arrays and differ in what sits beside
them: current suite2p writes ``reg_outputs.npy`` and ``detect_outputs.npy``,
while older output writes ``ops.npy``.

They are split so each can follow suite2p's own changes without disturbing the
other, and they are mutually exclusive: the legacy detector requires the absence
of every current marker.

Signatures from https://suite2p.readthedocs.io/en/latest/outputs/ and
``suite2p/run_s2p.py`` (MouseLand/suite2p).
"""

from __future__ import annotations

import re

from ..types import DirListing
from . import Confidence, Hit

CORE_ARRAYS = ("F.npy", "Fneu.npy", "spks.npy", "stat.npy", "iscell.npy")

CURRENT_MARKERS = ("reg_outputs.npy", "detect_outputs.npy")
CURRENT_COROBORATING = ("settings.npy", "db.npy")
LEGACY_MARKERS = ("ops.npy",)

CONDITIONAL_ARRAYS = ("redcell.npy", "zcorr.npy", "F_chan2.npy", "Fneu_chan2.npy")
EXPORTS = ("Fall.mat", "ophys.nwb")

MODALITY = "calcium imaging (segmentation output)"
PARENT_DIR = "suite2p"
PLANE_DIR = re.compile(r"^(?:plane\d+|combined)$", re.IGNORECASE)


def _has_core(listing: DirListing) -> bool:
    return all(listing.has_file(name) for name in CORE_ARRAYS)


def _context(listing: DirListing) -> list[str]:
    """Evidence common to both generations: extras, exports and location."""
    evidence = [n for n in CONDITIONAL_ARRAYS if listing.has_file(n)]
    evidence.extend(n for n in EXPORTS if listing.has_file(n))
    if PLANE_DIR.match(listing.name):
        evidence.append(f"{listing.name}/")
    if listing.path.parent.name.casefold() == PARENT_DIR:
        evidence.append(f"{PARENT_DIR}/")
    return evidence


class Suite2pDetector:
    """Current suite2p output."""

    name = "suite2p"
    modality = MODALITY

    def sniff_dir(self, listing: DirListing) -> Hit | None:
        if not _has_core(listing):
            return None
        markers = [n for n in CURRENT_MARKERS if listing.has_file(n)]
        if not markers:
            return None

        evidence = [*CORE_ARRAYS, *markers]
        evidence.extend(n for n in CURRENT_COROBORATING if listing.has_file(n))
        evidence.extend(_context(listing))

        return Hit(
            detector=self.name,
            modality=self.modality,
            confidence=Confidence.HIGH,
            evidence=tuple(evidence),
            variant="current",
        )


class Suite2pLegacyDetector:
    """suite2p output predating the ``reg_outputs``/``detect_outputs`` split.

    Also catches output carrying no generation marker at all — a pruned or
    partially copied tree — at reduced confidence, since the five core arrays
    together are still unmistakable.
    """

    name = "suite2p-legacy"
    modality = MODALITY

    def sniff_dir(self, listing: DirListing) -> Hit | None:
        if not _has_core(listing):
            return None
        if any(listing.has_file(n) for n in CURRENT_MARKERS):
            return None

        markers = [n for n in LEGACY_MARKERS if listing.has_file(n)]
        evidence = [*CORE_ARRAYS, *markers]
        evidence.extend(_context(listing))

        if markers:
            confidence = Confidence.HIGH
        else:
            confidence = Confidence.MEDIUM
            evidence.append("no generation marker — output cannot be dated")

        return Hit(
            detector=self.name,
            modality=self.modality,
            confidence=confidence,
            evidence=tuple(evidence),
            variant="legacy",
        )
