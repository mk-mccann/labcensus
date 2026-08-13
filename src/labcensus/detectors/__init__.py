"""Modality detectors: what a directory listing says the data is.

A detector decides from names alone. It never opens a file and imports no
third-party library, so a scan cannot read data even by accident and the CLI
starts fast.

Detectors see one directory's immediate contents. Reading a sidecar to enrich a
hit that has already fired is a later, gated step; reading anything to produce
one is not allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..types import DirListing

ENTRY_POINT_GROUP = "labcensus.detectors"


class Confidence(IntEnum):
    """How far a hit can be trusted, anchored to what actually matched.

    ``HIGH``
        A sentinel file or a required directory structure.
    ``MEDIUM``
        A distinctive pairing or naming grammar, but no sentinel.
    ``LOW``
        Extensions alone. Reported as possible, not confirmed.
    """

    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass(frozen=True, slots=True)
class Hit:
    """One detector's finding for one directory.

    ``evidence`` lists the names that matched, so any finding can be checked
    against the directory it came from.
    """

    detector: str
    modality: str
    confidence: Confidence
    evidence: tuple[str, ...]
    variant: str | None = None


class Detector(Protocol):
    name: str
    modality: str

    def sniff_dir(self, listing: DirListing) -> Hit | None: ...


def builtin_detectors() -> tuple[Detector, ...]:
    """The detectors shipped with labcensus."""
    from .behavior_deeplabcut import DeepLabCutDetector
    from .caimg_caiman import CaimanDetector
    from .caimg_suite2p import Suite2pDetector, Suite2pLegacyDetector
    from .ephys_openephys import OpenEphysDetector
    from .ephys_spikeglx import SpikeGLXDetector

    return (
        OpenEphysDetector(),
        SpikeGLXDetector(),
        Suite2pDetector(),
        Suite2pLegacyDetector(),
        CaimanDetector(),
        DeepLabCutDetector(),
    )


def load_detectors(*, include_plugins: bool = True) -> tuple[Detector, ...]:
    """Built-in detectors, plus any registered under ``labcensus.detectors``.

    Third parties add modalities by publishing a package with an entry point in
    that group.
    """
    detectors = list(builtin_detectors())
    if include_plugins:
        from importlib.metadata import entry_points

        for entry_point in sorted(
            entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name
        ):
            detectors.append(entry_point.load()())
    return tuple(detectors)


def sniff(listing: DirListing, detectors: tuple[Detector, ...]) -> tuple[Hit, ...]:
    """Every hit for one directory, strongest first.

    All detectors are asked, and every hit is returned: a recording and its
    spike-sorted output are two true findings, not a contest. Ties break on
    detector name.
    """
    hits = [hit for d in detectors if (hit := d.sniff_dir(listing)) is not None]
    hits.sort(key=lambda h: (-h.confidence, h.detector))
    return tuple(hits)
