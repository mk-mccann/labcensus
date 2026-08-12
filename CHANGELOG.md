# Changelog

Notable changes to labcensus. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `labcensus scan` now walks a tree and records it into a local SQLite index:
  one row per file and per directory, with size, allocated blocks, modification
  and access times, creation time where the platform provides one, owner and
  group, permissions, hardlink identity, and symlink targets. Unreadable paths
  are recorded as findings rather than raised, so a scan completes on storage it
  cannot fully read.

  Separating the walk from everything downstream means the expensive part — the
  `stat` calls, which dominate on network storage — is paid once. Detection,
  rollups and reporting all read the index afterwards without touching the
  storage again. The index stays on the machine that created it; nothing is
  transmitted anywhere, and writing it inside the tree being scanned is refused
  rather than warned about.

- Modality detectors for Open-Ephys (binary and legacy), SpikeGLX, suite2p
  (current and legacy output generations), CaImAn and DeepLabCut. Detectors
  classify a directory from its listing alone and import no third-party
  library, so a scan opens no data file. Each finding carries the evidence that
  produced it and a confidence level, and unrecognised directories are reported
  as such rather than guessed at.

### Changed

- **Licence changed from MIT to BSD-3-Clause.** `0.0.1` was published under the
  MIT License and **that release remains MIT** — a published artifact cannot be
  relicensed retroactively. Every release from this point onward is
  BSD-3-Clause.

  The terms stay permissive: commercial use, modification, distribution and
  private use are all still granted. The one added condition is clause 3, which
  says the copyright holder's name may not be used to endorse or promote derived
  products without written permission. This aligns labcensus with NeuroConv and
  the NWB reference tooling, which are also BSD-3-Clause.

- `FileStat.path` and `DirListing.path` are now `pathlib.PurePath` rather than
  `str`, with the flavour chosen by the backend that produced the path
  (`PureWindowsPath` on Windows, `PurePosixPath` on POSIX). `name` and `suffix`
  derive from it, which makes them correct for Windows drive paths, UNC paths,
  and POSIX filenames containing a backslash. Previously these were split with
  `posixpath`, which returned the whole path as the "name" on Windows.

## [0.0.1] - 2026-08-11

Name reservation. **Non-functional**: the CLI accepts a path argument and raises
`NotImplementedError`. Published under the MIT License.
