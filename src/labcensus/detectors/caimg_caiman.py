"""CaImAn output.

Two signals, of very different quality, which is why this detector reports two
confidence levels rather than one.

The good signal is the memory-map filename, built by
``caiman.paths.memmap_frames_filename`` (flatironinstitute/CaImAn, GPL-2.0 — the
*grammar* is reproduced here as a fact about the data format, not as copied
code)::

    f"{basename}_d1_{d1}_d2_{d2}_d3_{d3}_order_{order}_frames_{frames}.mmap"

That is highly distinctive, free at T0, and usually attached to the largest
files CaImAn leaves behind. Older releases emitted a trailing underscore before
the extension, so both generations are accepted.

CaImAn's other output is an ``.hdf5`` holding an ``estimates`` group, and this
detector deliberately does **not** claim it. Confirming that group means opening
the file — T4, which v1 does not do — and the extension collides with NWB,
SLEAP, MaxWell, Biocam, MEArec and DeepLabCut, whose own analysis output is
``.h5``. A first attempt reported bare HDF5 as low-confidence CaImAn and fired
on DeepLabCut's files in the same directory, which is precisely the overclaiming
that would cost the report its credibility.

So a CaImAn results file with no memory-map beside it is missed. That is the
right trade: an unattributed ``.hdf5`` belongs in a generic "HDF5, unidentified"
finding — a census result in its own right under graceful ignorance — not in a
detector that names a package it cannot actually confirm.
"""

from __future__ import annotations

import re

from ..types import DirListing
from . import Confidence, Hit

MMAP_GRAMMAR = re.compile(
    r"_d1_\d+_d2_\d+_d3_\d+_order_[CF]_frames_\d+_?\.mmap$",
    re.IGNORECASE,
)


class CaimanDetector:
    name = "caiman"
    modality = "calcium imaging (segmentation output)"

    def sniff_dir(self, listing: DirListing) -> Hit | None:
        mmaps = sorted(f.name for f in listing.files if MMAP_GRAMMAR.search(f.name))
        if not mmaps:
            return None

        evidence = [mmaps[0]]
        if len(mmaps) > 1:
            evidence.append(f"(+{len(mmaps) - 1} more *.mmap)")

        return Hit(
            detector=self.name,
            modality=self.modality,
            confidence=Confidence.HIGH,
            evidence=tuple(evidence),
            variant="memory-mapped",
        )
