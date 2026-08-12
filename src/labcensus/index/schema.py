"""Index schema.

Normalised so a path is stored once rather than once per file. A million-file
tree lands at roughly 81 bytes per file with indexes built, against about 215
for the same records as flat JSON lines — the saving is almost entirely
directory normalisation plus interning owners and suffixes. The directory tree
falls out as a side effect rather than as extra work.

Indexes are created *after* the bulk load, not declared with the tables, because
maintaining them per-insert costs far more than building them once at the end.
:data:`INDEXES` is applied by :meth:`~labcensus.index.writer.IndexWriter.finish`.
"""

from __future__ import annotations

#: Bumped when a released schema changes shape. Separate from the report's
#: version: two artifacts, two compatibility stories, and this is the one that
#: will move more often.
INDEX_SCHEMA_VERSION = 1

TABLES = """
CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans(
    id INTEGER PRIMARY KEY,
    root TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    tool_version TEXT NOT NULL,
    hostname TEXT,
    platform TEXT,
    n_dirs INTEGER,
    n_files INTEGER,
    n_errors INTEGER
);

-- A directory. parent_id is NULL for the scan root, so the tree is the table.
CREATE TABLE IF NOT EXISTS dirs(
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL,
    parent_id INTEGER,
    name TEXT NOT NULL,
    depth INTEGER NOT NULL
);

-- Interned: a million-file tree has a handful of principals and a few hundred
-- distinct suffixes, so storing either as text per row is pure waste.
CREATE TABLE IF NOT EXISTS owners(
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    raw_id TEXT NOT NULL,
    UNIQUE(kind, raw_id)
);

CREATE TABLE IF NOT EXISTS suffixes(
    id INTEGER PRIMARY KEY,
    value TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS files(
    id INTEGER PRIMARY KEY,
    dir_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    -- The original bytes, only when the name was not valid UTF-8. Non-NULL is
    -- itself the "this name is not text" flag.
    name_raw BLOB,
    suffix_id INTEGER,
    size INTEGER NOT NULL,
    -- Allocated blocks. Diverges from size under sparse files, compression and
    -- deduplication, which is why the headline volume can disagree with what
    -- the storage administrator's quota tool reports.
    blocks INTEGER,
    mtime REAL NOT NULL,
    -- True creation time where the platform has one. NULL on Linux rather than
    -- silently substituting st_ctime, which is a metadata-change time.
    btime REAL,
    atime REAL,
    owner_id INTEGER,
    group_id INTEGER,
    mode INTEGER,
    ino INTEGER,
    dev INTEGER,
    nlink INTEGER,
    islink INTEGER NOT NULL,
    -- Unresolved symlink target. What makes a git-annex or DataLad tree
    -- distinguishable from a large dataset occupying no space.
    link_target TEXT
);

-- Unreadable paths are report content, not log noise: a tree nobody can read is
-- exactly the kind of thing a PI needs told.
CREATE TABLE IF NOT EXISTS errors(
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    reason TEXT NOT NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_files_dir ON files(dir_id);
CREATE INDEX IF NOT EXISTS ix_files_suffix ON files(suffix_id);
CREATE INDEX IF NOT EXISTS ix_files_owner ON files(owner_id);
CREATE INDEX IF NOT EXISTS ix_dirs_parent ON dirs(scan_id, parent_id);
CREATE INDEX IF NOT EXISTS ix_errors_scan ON errors(scan_id);
"""

#: Applied while loading. Ordinarily reckless, and justified here because the
#: index is a *derived* artifact — if the machine dies mid-walk the answer is to
#: scan again, not to recover a half-written database.
BUILD_PRAGMAS = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
PRAGMA temp_store=MEMORY;
PRAGMA cache_size=-131072;
"""

#: Restored once the load is done and the file is going to be queried.
QUERY_PRAGMAS = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
"""
