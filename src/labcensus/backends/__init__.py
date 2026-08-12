"""Filesystem backends: the only code permitted to touch a filesystem.

A backend turns whatever its platform offers into :class:`~labcensus.types.FileStat`
and :class:`~labcensus.types.DirListing` records. Detectors and heuristics see
only those, which is what allows an S3 or SSH backend later to be an addition
rather than a rewrite.

It is also the only layer allowed to *parse* a path, because it is the only
layer that knows which platform produced it.
"""

from __future__ import annotations

from .local import LocalBackend

__all__ = ["LocalBackend"]
