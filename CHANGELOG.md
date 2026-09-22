# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.1] - 2026-09-22
### Fixed
- `is_flipper_port` referenced `serial.tools.list_ports.ListPortInfo`, which doesn't exist on that module
  (the class actually lives in `serial.tools.list_ports_common`). This crashed on import on any Python
  version that evaluates annotations eagerly (≤3.12) — including the Homebrew formula, which pins
  `python@3.12`.

## [1.0.0] - 2026-09-21
Initial release
