"""Filesystem backends: the only code that touches a filesystem.

A backend turns what its platform offers into :class:`~labcensus.types.FileStat`
and :class:`~labcensus.types.DirListing` records, and is the only layer that
parses a path.
"""

from __future__ import annotations

from .local import LocalBackend

__all__ = ["LocalBackend"]
