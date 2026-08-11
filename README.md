# labcensus

A read-only scanner that tells a PI what is actually on their lab's storage — modalities, formats, volume, staleness, standards compliance, and orphaned data at risk of becoming unreadable — emitted as a single local HTML report.

**Read-only. Local-only.** labcensus never writes, moves, or converts a file, and never makes a network call. Everything it produces is a local JSON/HTML report on your machine.

> Status: early development (pre-M0). Not yet published to PyPI.

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

### From PyPI (once published)

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
labcensus scan /path/to/lab/storage -o report.json
```

(Full usage docs land alongside the M0 walking-skeleton milestone.)

## Why read-only, why local

This tool is meant to run on a PI's NAS the first time you ask, with nothing to configure and nothing sent anywhere. Trust is the product.

## License

MIT
