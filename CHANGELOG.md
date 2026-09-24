# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.2.0] - 2026-09-24
### Changed
- PNG output is now 1-bit grayscale instead of 8-bit grayscale (about a 20% smaller file).
- **Breaking:** output path is now an explicit `-o`/`--output` flag instead of the second positional
  argument. `flipshot port output.png` no longer works - use `flipshot port -o output.png` instead.
  This also makes it possible to set the output path without specifying a port: `flipshot -o output.png`.

## [1.1.0] - 2026-09-23
### Added
- `-b` / `--burst [N] [M]` captures N screenshots with M milliseconds between them.
  N defaults to 10, and `-1` keeps capturing until the script is stopped. M defaults to 1000
  and must be in the range 100–5000.
- `-v` as the short form of `--version`.

## [1.0.1] - 2026-09-22
### Fixed
- `is_flipper_port` referenced `serial.tools.list_ports.ListPortInfo`, which doesn't exist in that module
  (the class lives in `serial.tools.list_ports_common`). This crashed on import for any Python
  version that evaluates annotations eagerly (≤3.12), including Python 3.12, which the Homebrew formula pins.

## [1.0.0] - 2026-09-21
Initial release.
