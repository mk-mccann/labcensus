"""SpikeGLX recordings: a ``.bin`` beside an identically-stemmed ``.meta``.

Signature adapted from the SpikeGLX file-naming documentation
(https://billkarsh.github.io/SpikeGLX/help/parsing/) and from
``neo.rawio.spikeglxrawio`` (NeuralEnsemble/python-neo, BSD-3-Clause), which
finds recordings the same way — scan for ``.meta``, derive the ``.bin``.

The pairing alone is already close to collision-free in a neuroscience tree, but
it is cheap to also check the stem grammar, so a coincidental ``X.bin``/``X.meta``
elsewhere on a NAS does not report as a Neuropixels recording. Grammar match is
the difference between HIGH and MEDIUM rather than between a hit and silence —
an unrecognised naming scheme is still probably SpikeGLX, and saying so at
reduced confidence is more useful than staying quiet.

``tcat`` appears where CatGT has concatenated trials, which is common enough in
a processed tree that omitting it would miss real recordings.
"""

from __future__ import annotations

import re

from ..types import DirListing
from . import Confidence, Hit

DATA_SUFFIX = ".bin"
META_SUFFIX = ".meta"

STREAM_GRAMMAR = re.compile(
    r"_g\d+_t(?:\d+|cat)\.(?:imec\d+\.(?:ap|lf)|nidq|obx\d+\.obx)$",
    re.IGNORECASE,
)


class SpikeGLXDetector:
    name = "spikeglx"
    modality = "extracellular ephys"

    def sniff_dir(self, listing: DirListing) -> Hit | None:
        stems = sorted(
            f.path.stem
            for f in listing.files
            if f.suffix == DATA_SUFFIX
            and listing.has_file(f.path.with_suffix(META_SUFFIX).name)
        )
        if not stems:
            return None

        recognised = [s for s in stems if STREAM_GRAMMAR.search(s)]
        confidence = Confidence.HIGH if recognised else Confidence.MEDIUM

        evidence = [f"{s}{DATA_SUFFIX} + {s}{META_SUFFIX}" for s in stems[:3]]
        if len(stems) > 3:
            evidence.append(f"(+{len(stems) - 3} more paired streams)")
        if streams := self._stream_kinds(recognised):
            evidence.append(f"streams: {', '.join(streams)}")

        return Hit(
            detector=self.name,
            modality=self.modality,
            confidence=confidence,
            evidence=tuple(evidence),
            variant=None,
        )

    def _stream_kinds(self, stems: list[str]) -> list[str]:
        kinds = set()
        for stem in stems:
            lowered = stem.lower()
            if ".ap" in lowered:
                kinds.add("ap")
            if ".lf" in lowered:
                kinds.add("lf")
            if ".nidq" in lowered:
                kinds.add("nidq")
            if ".obx" in lowered:
                kinds.add("obx")
        return sorted(kinds)
