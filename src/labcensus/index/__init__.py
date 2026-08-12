"""The scan index: a local SQLite file holding one row per path.

Separating the walk from everything downstream is what makes the tool usable on
a real lab NAS. The walk is bound by ``stat`` calls — on the order of an hour
for ten million files over SMB — and it should be paid exactly once. Detection,
rollups and reporting all read the index afterwards, as often as needed, without
touching the storage again.

The index never leaves the machine that created it. Nothing is transmitted
anywhere, which keeps a scan at any institution a matter of that institution's
own internal processing.
"""

from __future__ import annotations

from .schema import INDEX_SCHEMA_VERSION
from .writer import IndexWriter

__all__ = ["INDEX_SCHEMA_VERSION", "IndexWriter"]
