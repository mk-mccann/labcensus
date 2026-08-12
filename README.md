# labcensus

A read-only scanner for scientists that tells you what is actually on your lab's storage — modalities, formats, volume, staleness, standards compliance, and orphaned data at risk of becoming unreadable — emitted as a single local HTML report.

**Read-only. Local-only.** labcensus never writes, moves, or converts a file inside the storage it scans, and never makes a network call. The index and report it produces stay on your machine — nothing is transmitted anywhere.

> **Status: early, and honest about it.** `labcensus scan` walks a tree and
> reports what is on it. It does not yet recognise data formats, score
> staleness, or emit an HTML report. The published `v0.0.1` on PyPI is a
> non-functional name reservation; build from source to try the scanner.

## Installation

### Requirements

- Python ≥ 3.10
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### From source (current, pre-release)

```bash
git clone <repo-url> labcensus
cd labcensus
uv sync
```

`uv sync` creates a `.venv` and installs labcensus plus its runtime dependencies (`fsspec`, `typer`, `pydantic`, `jinja2`) as pinned in `uv.lock`.

To also install dev tooling (pytest, ruff) for contributing:

```bash
uv sync --group dev
```

Run the CLI inside the managed environment with `uv run`:

```bash
uv run labcensus scan /path/to/lab/storage
```

Or activate the virtualenv directly:

```bash
source .venv/bin/activate
labcensus scan /path/to/lab/storage
```

### From PyPI

Zero-install, one-off usage via `uvx` — the promoted way to try labcensus on a NAS:

```bash
uvx labcensus scan /path/to/lab/storage
```

Or install it into a project/environment:

```bash
uv pip install labcensus
# or
pip install labcensus
```

## Usage

```bash
labcensus scan /path/to/lab/storage
```

It walks the tree, records every file and directory into a local SQLite index,
and prints a summary: how much is there, what kinds of file, which directories
hold the most, how old it all is, and what could not be read.

```
/mnt/nas/lab
  6,319 files in 655 directories, 9 deep, scanned in 0.2s
  133.6 MB apparent, 148.3 MB on disk
  1 owner(s), 3 symlink(s)

By file type
  .py                    3,551 files       47.0 MB
  .so                      101 files       44.7 MB
  (no extension)         1,547 files       26.9 MB

Largest directories
       24.6 MB       27 files  lab/rec1/continuous
...
```

The index is written next to where you run the command, or wherever `-o` says.
It is **refused** if it would land inside the tree being scanned, because a tool
that writes into the storage it is auditing is not a read-only tool.

| Option | |
|---|---|
| `-o, --output PATH` | Where to write the index. Default `./labcensus-index.db` |
| `--top N` | Rows in each summary table. Default 10 |
| `--progress / --no-progress` | Progress to stderr. On by default when stderr is a terminal |

Separating the walk from everything else is deliberate. The `stat` calls
dominate — on network storage a large tree can take an hour — so the walk is
paid once and written down. Everything that comes later reads the index instead
of the storage.

### What it does not do yet

Format detection, staleness and orphan scoring, standards compliance checks, and
the HTML report. Those read the index that `scan` now produces.

## License

BSD 3-Clause. Free to use, modify, and distribute, commercially or otherwise;
the one added condition is that the copyright holder's name may not be used to
endorse or promote derived products without written permission.
