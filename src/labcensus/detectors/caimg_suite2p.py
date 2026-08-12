"""suite2p segmentation output, as two detectors — one per output generation.

Signatures from the suite2p documentation
(https://suite2p.readthedocs.io/en/latest/outputs/) and ``suite2p/run_s2p.py``
(MouseLand/suite2p, GPL-3.0 — the file names are reproduced as facts about the
output format, not as copied code).

Both generations write the same five core arrays. They differ in what sits
beside them:

===============  ==================================================
Current          ``reg_outputs.npy`` + ``detect_outputs.npy``, both
                 documented as always saved
Legacy           ``ops.npy``, which the current documentation no
                 longer lists
===============  ==================================================

Split into two detectors rather than one with a variant, because the two file
sets are disjoint and each will drift on its own schedule — suite2p has already
changed this once. A third generation becomes a third detector, and neither
existing one has to be touched or re-tested. The cost is a little duplication
and one extra pass per directory, which is nothing next to the ``scandir`` the
walker has already paid for.

The generations are mutually exclusive by construction: the legacy detector
requires the *absence* of every current marker, so a directory can never satisfy
both. Same shape as the Open-Ephys pair, where legacy is defined by the absence
of ``structure.oebin``.

``settings.npy`` and ``db.npy`` are written by current ``run_s2p.py`` but are
absent from the documented output list, so they corroborate the current
generation without being required by it.
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
    """Evidence common to both generations: extras, exports, and location."""
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

    Also catches output that carries no generation marker at all — a partial
    copy, or a tree someone pruned. That still has the five core arrays, which
    together are unmistakable, so it is reported at reduced confidence with the
    reason stated rather than dropped. Old and half-copied output is exactly
    what a census exists to surface.
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
