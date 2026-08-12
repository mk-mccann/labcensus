"""Open-Ephys GUI recordings, both on-disk generations.

Signature constants adapted from the Open-Ephys GUI documentation
(https://open-ephys.github.io/gui-docs/User-Manual/Data-formats/Binary-format.html)
and from ``neo.rawio.openephysbinaryrawio`` / ``openephysrawio``
(NeuralEnsemble/python-neo, BSD-3-Clause).

The two generations are distinguished by one test, and the order matters.
Upstream tools test for ``*.continuous`` first and otherwise fall through to
*any* ``.dat`` — safe for them, because a human has already said "this folder is
Open-Ephys". As a classifier that would claim a large share of a lab NAS, since
six neo readers alone claim ``.dat``. So the strong sentinel is checked first
and a bare ``.dat`` never contributes anything.

Reporting the generation matters commercially as much as technically: legacy
data is the most likely in this ecosystem to become unreadable, so "legacy
Open-Ephys, newest file 2019" is the finding most likely to start a
conversation.
"""

from __future__ import annotations

from ..types import DirListing
from . import Confidence, Hit

BINARY_SENTINEL = "structure.oebin"
BINARY_SUBDIRS = ("continuous", "events", "spikes")

LEGACY_SUFFIX = ".continuous"
LEGACY_COMPANION_SUFFIXES = (".spikes", ".events", ".openephys")

SETTINGS_FILE = "settings.xml"


class OpenEphysDetector:
    name = "open-ephys"
    modality = "extracellular ephys"

    def sniff_dir(self, listing: DirListing) -> Hit | None:
        if listing.has_file(BINARY_SENTINEL):
            return self._hit("binary", self._binary_evidence(listing))

        continuous = [f for f in listing.files if f.suffix == LEGACY_SUFFIX]
        if continuous:
            return self._hit("legacy", self._legacy_evidence(listing, len(continuous)))

        return None

    def _binary_evidence(self, listing: DirListing) -> list[str]:
        evidence = [BINARY_SENTINEL]
        evidence.extend(f"{d}/" for d in BINARY_SUBDIRS if listing.has_subdir(d))
        if listing.has_file(SETTINGS_FILE):
            evidence.append(SETTINGS_FILE)
        return evidence

    def _legacy_evidence(self, listing: DirListing, count: int) -> list[str]:
        evidence = [f"*{LEGACY_SUFFIX} ×{count}"]
        present = {f.suffix for f in listing.files}
        evidence.extend(f"*{s}" for s in LEGACY_COMPANION_SUFFIXES if s in present)
        if listing.has_file(SETTINGS_FILE):
            evidence.append(SETTINGS_FILE)
        return evidence

    def _hit(self, variant: str, evidence: list[str]) -> Hit:
        return Hit(
            detector=self.name,
            modality=self.modality,
            confidence=Confidence.HIGH,
            evidence=tuple(evidence),
            variant=variant,
        )
