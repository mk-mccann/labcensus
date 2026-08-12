# labcensus

A read-only scanner for scientists that tells you what is actually on your lab's storage — modalities, formats, volume, staleness, standards compliance, and orphaned data at risk of becoming unreadable — emitted as a single local HTML report.

**Read-only. Local-only.** labcensus never writes, moves, or converts a file, and never makes a network call. Everything it produces is a local JSON/HTML report on your machine.

> **Status: name reserved, not yet functional.** `v0.0.1` is a placeholder
> release. The scanner is in active development — the commands below describe
> the intended v1 interface and **do not work yet**. Watch the repo for the
> first working release.

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

## Usage (planned)

The intended v1 interface — not yet implemented:

```bash
labcensus scan /path/to/lab/storage -o report.json
```

Full usage docs land with the M0 walking-skeleton milestone.

## License

BSD 3-Clause. Free to use, modify, and distribute, commercially or otherwise;
the one added condition is that the copyright holder's name may not be used to
endorse or promote derived products without written permission.
