"""Modality detectors: the layer that turns a directory listing into a finding.

A detector answers "what is this directory?" from names alone. It never opens a
file and never imports a third-party library — the signature knowledge is
vendored constants, not a dependency. That keeps ``labcensus --help`` fast
(``import neo.rawio`` alone costs ~277 ms), keeps the read-only guarantee
trivially true, and avoids inheriting upstream pins.

Detectors see :class:`~labcensus.types.DirListing`, which is one directory's
immediate contents. They must decide from that plus cheap name arithmetic.
Reading a sidecar to *enrich* an already-fired hit is a later, gated step;
reading anything to *produce* a hit is not allowed, because a multi-TB NAS over
SMB cannot afford it.

Only ``sniff_dir`` exists. The spec also sketches ``sniff_file`` and ``enrich``;
neither has a consumer yet, and an interface designed without one is usually
wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..types import DirListing

ENTRY_POINT_GROUP = "labcensus.detectors"


class Confidence(IntEnum):
    """How much a hit should be trusted, ordered so hits can be ranked.

    Deliberately three coarse levels rather than the float the spec sketches.
    A float invites arithmetic nobody can justify — there is no meaningful
    sense in which a sentinel match is 0.9 rather than 0.85 — and the report
    renders these as words regardless.

    The scale is anchored to *what matched*, not to a feeling:

    ``HIGH``
        A sentinel file, or a required directory structure. Nothing else
        produces this.
    ``MEDIUM``
        A distinctive pairing or naming grammar, but no sentinel.
    ``LOW``
        Extensions alone. Renders as "possible X — not confirmed".
    """

    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass(frozen=True, slots=True)
class Hit:
    """One detector's finding for one directory.

    ``evidence`` is the point of this record. A report that says "Open-Ephys,
    high confidence" is unfalsifiable to a sceptical PI; one that says
    "matched ``structure.oebin``, ``continuous/``" can be checked in ten
    seconds. It is also what makes a low-confidence hit self-explaining rather
    than merely hedged, and it is the field a downstream archaeology tool would
    consume.
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
    """The detectors shipped with labcensus.

    Imported inside the function so that merely importing this module — which
    the CLI does — does not pay for every detector module.
    """
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
    that group; core never changes. Built-ins are *not* routed through the same
    mechanism, because scanning entry points costs real time on every
    invocation and our own detectors do not need discovering.

    ``importlib.metadata`` is imported lazily for the same reason.
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

    All detectors are asked. Returning every hit rather than a winner is
    deliberate: a tree holding both an Open-Ephys recording and its spike-sorted
    output is two true findings, and the report should link them rather than
    pick one. Ties break on detector name so output is deterministic.
    """
    hits = [hit for d in detectors if (hit := d.sniff_dir(listing)) is not None]
    hits.sort(key=lambda h: (-h.confidence, h.detector))
    return tuple(hits)
