"""DeepLabCut projects and their analysis output.

Two shapes, both verified against DeepLabCut/DeepLabCut source.

A **project** directory is created by ``deeplabcut.create_project.new`` with a
fixed layout — ``config.yaml`` at the root alongside ``labeled-data/``,
``training-datasets/``, ``dlc-models/`` and ``videos/``. That is a required
directory structure, so it earns HIGH confidence, and it is worth detecting on
its own: a project directory is where the labelling effort lives, which is
usually the least reproducible thing in the tree.

**Analysis output** is named from the scorer string built in
``auxiliaryfunctions.get_scorer_name``::

    scorer = "DLC_" + netname + "_" + Task + date + "shuffle" + shuffle + "_" + iterations

and written as ``<video><scorer>.h5`` (plus ``filtered`` variants and a matching
``.csv``). Projects created before DLC 2.1 used ``DeepCut`` in place of ``DLC``,
so both are accepted.

Neither shape is detectable from extensions: DeepLabCut declares ``.h5`` and
``.csv``, which are among the least diagnostic suffixes in the ecosystem.
"""

from __future__ import annotations

import re

from ..types import DirListing
from . import Confidence, Hit

PROJECT_CONFIG = "config.yaml"
PROJECT_DIRS = ("labeled-data", "training-datasets", "dlc-models", "videos")
MIN_PROJECT_DIRS = 2

SCORER_GRAMMAR = re.compile(r"(DLC|DeepCut)_.+shuffle\d+_\d+", re.IGNORECASE)
OUTPUT_SUFFIXES = (".h5", ".csv")


class DeepLabCutDetector:
    name = "deeplabcut"
    modality = "behavior (pose estimation)"

    def sniff_dir(self, listing: DirListing) -> Hit | None:
        if hit := self._project(listing):
            return hit
        return self._output(listing)

    def _project(self, listing: DirListing) -> Hit | None:
        if not listing.has_file(PROJECT_CONFIG):
            return None
        present = [f"{d}/" for d in PROJECT_DIRS if listing.has_subdir(d)]
        if len(present) < MIN_PROJECT_DIRS:
            return None
        return Hit(
            detector=self.name,
            modality=self.modality,
            confidence=Confidence.HIGH,
            evidence=(PROJECT_CONFIG, *present),
            variant="project",
        )

    def _output(self, listing: DirListing) -> Hit | None:
        matches = sorted(
            f.name
            for f in listing.files
            if f.suffix in OUTPUT_SUFFIXES and SCORER_GRAMMAR.search(f.name)
        )
        if not matches:
            return None

        evidence = [matches[0]]
        if len(matches) > 1:
            evidence.append(f"(+{len(matches) - 1} more scorer-named files)")
        if any(
            m.lower().startswith("deepcut") or "deepcut_" in m.lower() for m in matches
        ):
            evidence.append("DeepCut scorer name (pre-DLC 2.1)")

        return Hit(
            detector=self.name,
            modality=self.modality,
            confidence=Confidence.HIGH,
            evidence=tuple(evidence),
            variant="analysis output",
        )
