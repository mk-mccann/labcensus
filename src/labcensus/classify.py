"""Detector rollups over a finished index.

The bridge between reading an index (`index/reader.py`) and recognising what
is in it (`detectors/`). Lives at the top level, alongside `walker.py`,
rather than inside `index/`, so `index/` keeps the property it already has:
nothing in it knows detectors exist.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .detectors import Confidence, Detector, Hit, sniff
from .index.reader import dir_ids_by_path, iter_dir_listings, subtree_size
from .index.summary import TOP_N


@dataclass(frozen=True)
class ConfidenceCounts:
    """How many hits in a group landed at each confidence level."""

    high: int = 0
    medium: int = 0
    low: int = 0


@dataclass(frozen=True)
class SampleHit:
    """One directory's hit from one detector, kept as a representative example.

    ``size`` is this directory's full subtree — what the finding actually
    represents — not just the files sitting directly in it.
    """

    path: str
    size: int
    confidence: Confidence
    variant: str | None
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class DetectorRow:
    """One detector's rollup across every directory it matched.

    ``n_dirs`` counts directories where *this* detector fired. ``total_size``
    sums each matched directory's full subtree (via
    :func:`index.reader.subtree_size`), not just its immediate files — a
    sentinel file (e.g. Open-Ephys's ``structure.oebin``, DeepLabCut's
    ``config.yaml``) is often small while the data it identifies sits one or
    more levels below it.

    A directory matched by more than one detector (e.g. a recording and its
    spike-sorted output sitting together — two true findings, not a
    collision) is credited in full to every detector it matched. Summing
    ``total_size`` across every row can therefore exceed the rollup's own
    ``size_with_hits``, which counts each matched directory once regardless
    of how many detectors fired on it. Both numbers are correct; they answer
    different questions — "how big is this finding" vs. "how much of the
    tree did we recognise at all."
    """

    detector: str
    modality: str
    n_dirs: int
    total_size: int
    confidence: ConfidenceCounts
    samples: tuple[SampleHit, ...]


@dataclass(frozen=True)
class UnrecognisedRow:
    """One directory where no detector fired, kept as a representative example.

    ``size`` is this directory's own immediate files only — there is no
    finding here to size a subtree for.
    """

    path: str
    size: int


@dataclass(frozen=True)
class Rollup:
    """What classification found, ready to render.

    ``n_dirs_with_hits``, ``size_with_hits`` and ``size_without_hits`` use
    each directory's *immediate* files, counted exactly once per directory
    regardless of how many detectors matched it — a clean partition, so
    ``total_size == size_with_hits + size_without_hits`` always holds. This
    answers "how much of the tree did we say something about," a different
    question from any single ``DetectorRow.total_size`` (see its docstring).
    """

    root: str
    n_dirs: int
    n_dirs_with_hits: int
    n_dirs_without_hits: int
    total_size: int
    size_with_hits: int
    size_without_hits: int
    detectors: tuple[DetectorRow, ...]
    sample_unrecognised: tuple[UnrecognisedRow, ...]


class _DetectorAccumulator:
    """Mutable running totals for one detector, finalised by `_build_row`."""

    def __init__(self, modality: str) -> None:
        self.modality = modality
        self.n_dirs = 0
        self.total_size = 0
        self.confidence = {Confidence.HIGH: 0, Confidence.MEDIUM: 0, Confidence.LOW: 0}
        self.samples: list[SampleHit] = []

    def add(self, hit: Hit, *, path: str, subtree_size: int) -> None:
        self.n_dirs += 1
        self.total_size += subtree_size
        self.confidence[hit.confidence] += 1
        self.samples.append(
            SampleHit(
                path=path,
                size=subtree_size,
                confidence=hit.confidence,
                variant=hit.variant,
                evidence=hit.evidence,
            )
        )


def _build_row(detector: str, acc: _DetectorAccumulator, top_n: int) -> DetectorRow:
    samples = tuple(
        sorted(acc.samples, key=lambda s: (-s.confidence, -s.size, s.path))[:top_n]
    )
    return DetectorRow(
        detector=detector,
        modality=acc.modality,
        n_dirs=acc.n_dirs,
        total_size=acc.total_size,
        confidence=ConfidenceCounts(
            high=acc.confidence[Confidence.HIGH],
            medium=acc.confidence[Confidence.MEDIUM],
            low=acc.confidence[Confidence.LOW],
        ),
        samples=samples,
    )


def classify_index(
    con: sqlite3.Connection,
    *,
    detectors: tuple[Detector, ...],
    top_n: int = TOP_N,
) -> Rollup:
    """Classify every directory in the finished scan held by ``con``.

    Runs every detector in ``detectors`` against every directory the index
    holds, via the same ``sniff()`` entry point a live scan would use, and
    rolls the results up per detector. Computed fresh on every call;
    nothing is written back into ``con``.

    Grouping is by detector, not modality: some detectors share a modality
    with no cross-suppression logic between them (``open-ephys`` and
    ``spikeglx`` both report "extracellular ephys"), so a directory could in
    principle satisfy both — a modality-level sum would double-count it.

    Args:
        con (sqlite3.Connection): Connection to the index database.
        detectors (tuple[Detector, ...]): The detectors to run. Callers
            decide whether to include third-party plugins by what they pass
            here — this function has no opinion.
        top_n (int): Sample hits kept per detector, and unrecognised
            directories kept as samples.

    Returns:
        Rollup: The classification rollup, ready to render.

    Raises:
        IncompleteIndexError: If the index holds no finished scan.
    """
    dir_ids = dir_ids_by_path(con)

    groups: dict[str, _DetectorAccumulator] = {}
    n_dirs = n_dirs_with_hits = 0
    size_with_hits = size_without_hits = 0
    unrecognised: list[UnrecognisedRow] = []

    for listing in iter_dir_listings(con):
        n_dirs += 1
        dir_size = sum(f.size for f in listing.files)
        hits = sniff(listing, detectors)

        if not hits:
            size_without_hits += dir_size
            unrecognised.append(UnrecognisedRow(path=str(listing.path), size=dir_size))
            continue

        n_dirs_with_hits += 1
        size_with_hits += dir_size
        hit_size = subtree_size(con, dir_ids[listing.path])
        for hit in hits:
            acc = groups.setdefault(hit.detector, _DetectorAccumulator(hit.modality))
            acc.add(hit, path=str(listing.path), subtree_size=hit_size)

    detector_rows = tuple(
        sorted(
            (_build_row(name, acc, top_n) for name, acc in groups.items()),
            key=lambda row: (-row.total_size, row.detector),
        )
    )
    sample_unrecognised = tuple(
        sorted(unrecognised, key=lambda row: (-row.size, row.path))[:top_n]
    )

    # dir_ids_by_path already confirmed a finished scan exists, so this can
    # read root directly without a second IncompleteIndexError check.
    (root,) = con.execute(
        "SELECT root FROM scans WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()

    return Rollup(
        root=str(root),
        n_dirs=n_dirs,
        n_dirs_with_hits=n_dirs_with_hits,
        n_dirs_without_hits=n_dirs - n_dirs_with_hits,
        total_size=size_with_hits + size_without_hits,
        size_with_hits=size_with_hits,
        size_without_hits=size_without_hits,
        detectors=detector_rows,
        sample_unrecognised=sample_unrecognised,
    )
