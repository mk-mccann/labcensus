"""NWB (Neurodata Without Borders) container files.

NWB is a storage standard, not an acquisition modality: one ``.nwb`` file can
hold ephys, imaging or behaviour data, already packaged for archival and
reuse. Detected by extension alone — but ``.nwb`` is reserved for the
standard specifically, unlike the bare ``.hdf5``/``.h5`` extension it is
built on, which collides with SLEAP, MaxWell, Biocam, MEArec, CaImAn and
DeepLabCut's own output (see ``caimg_caiman.py``). The same reasoning that
gives Open-Ephys's bare ``.continuous`` count HIGH confidence applies here: a
distinctive, low-collision extension earns HIGH even without a sentinel
filename or a required directory structure.
"""

from __future__ import annotations

from ..types import DirListing
from . import Confidence, Hit


class NWBDetector:
    name = "nwb"
    modality = "standardized neurophysiology (NWB)"

    def sniff_dir(self, listing: DirListing) -> Hit | None:
        matches = sorted(f.name for f in listing.files if f.suffix == ".nwb")
        if not matches:
            return None

        evidence = [matches[0]]
        if len(matches) > 1:
            evidence.append(f"(+{len(matches) - 1} more *.nwb)")

        return Hit(
            detector=self.name,
            modality=self.modality,
            confidence=Confidence.HIGH,
            evidence=tuple(evidence),
        )
