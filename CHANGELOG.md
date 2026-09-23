# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]
### Added
- `-b` / `--burst [N] [M]` captures N screenshots with M milliseconds between them.
  N defaults to 10 and can be `-1` to run until the script is stopped. M defaults to 1000,
  in the range 100–5000.
- `-v` as the short form of `--version`.

## [1.0.1] - 2026-09-22
### Fixed
- `is_flipper_port` referenced `serial.tools.list_ports.ListPortInfo`, which doesn't exist on that module
  (the class actually lives in `serial.tools.list_ports_common`). This crashed on import on any Python
  version that evaluates annotations eagerly (≤3.12) — including the Homebrew formula, which pins
  `python@3.12`.

## [1.0.0] - 2026-09-21
Initial release
