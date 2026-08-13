"""Index schema.

Normalised so a path is stored once rather than once per file, which also makes
the directory tree available for free. Indexes are built after the bulk load
rather than declared with the tables.
"""

from __future__ import annotations

#: Bumped when a released schema changes shape. Separate from the report's own
#: version.
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

-- Interned: a large tree has few principals and few distinct suffixes.
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
    -- Original bytes, set only when the name was not valid UTF-8.
    name_raw BLOB,
    suffix_id INTEGER,
    size INTEGER NOT NULL,
    -- Allocated blocks, which diverges from size under sparse files,
    -- compression and deduplication.
    blocks INTEGER,
    mtime REAL NOT NULL,
    -- True creation time where the platform has one; NULL otherwise.
    btime REAL,
    atime REAL,
    owner_id INTEGER,
    group_id INTEGER,
    mode INTEGER,
    ino INTEGER,
    dev INTEGER,
    nlink INTEGER,
    islink INTEGER NOT NULL,
    -- Unresolved symlink target.
    link_target TEXT
);

-- Unreadable paths are findings, not log noise.
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

#: Applied while loading. Durability is traded away because the index is a
#: derived artifact: if a walk dies, the answer is to scan again.
BUILD_PRAGMAS = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
PRAGMA temp_store=MEMORY;
PRAGMA cache_size=-131072;
"""

#: Restored once the load is done.
QUERY_PRAGMAS = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
"""
