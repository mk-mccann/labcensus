"""The scan index: a local SQLite file holding one row per path.

The walk is paid once and written here; detection, rollups and reporting read
the index afterwards without touching the storage again. The index stays on the
machine that created it.
"""

from __future__ import annotations

from .schema import INDEX_SCHEMA_VERSION
from .writer import IndexWriter

__all__ = ["INDEX_SCHEMA_VERSION", "IndexWriter"]
