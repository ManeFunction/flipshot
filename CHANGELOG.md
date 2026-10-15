# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.4.0] - 2026-10-15
### Added
- Screenshots are now saved upright. A Flipper in left-handed mode used to produce upside-down images;

### Changed
- **Breaking:** Python 3.9 or newer is now required (it was 3.8).
- `-s` / `--scale` now takes a plain number: `-s 3` instead of `-s x3`. The `x3` form is still accepted.
- `-o shot` now saves `shot.png` when the name has no extension, the same as burst mode already did
  (`shot-1.png`, ...).
- Long options can no longer be abbreviated (`--out` must be written `--output`); this is what lets
  `--bur 5` be reported as an error instead of being silently ignored.
- Errors are printed to stderr. Losing the connection mid-capture is reported as such, instead of
  "Could not open ... Close qFlipper"; the "no screen frame" error now includes the cable hint.
- A burst stopped with SIGTERM now ends the same way as with Ctrl+C.

### Fixed
- A burst could lose sync with the Flipper after a short read timeout, leaving later screenshots as
  duplicates of an earlier one (or crashing on garbage data). A message that has started is now read to the end, and a stalled one is reported as an error.
- `-b5` is now accepted, and `-bp`, `-pb` and `--bur 5` report an error instead of silently taking one plain screenshot (and dropping `-p`).
- Ctrl+C during a single screenshot exits cleanly ("Cancelled.") instead of printing a traceback.
- A failed save (for example `-o missing-folder/x.png`) is reported as an error instead of a traceback, and a malformed message from the device is reported instead of crashing with `IndexError`.
- A failure while stopping the screen stream no longer discards a screenshot that was already captured.
- `flipshot.exe --version` on Windows reported `0.0.0-dev`; it now shows the release version.
- Timeouts no longer misbehave when the system clock is adjusted while a screenshot is being taken
  (a burst pause could hang, or the first-frame wait could end instantly).

## [1.3.0] - 2026-10-06
### Added
- `-s` / `--scale xN` enlarges the screenshot N times (N from 1 to 10), so each Flipper pixel becomes
  an NxN block, e.g. `-s x3` gives 384x192.
- `-p` / `--paint` draws the screenshot in the Flipper's own colors (orange `#fe8a2c` background instead
  of white), saved as a 2-color indexed PNG.

### Changed
- `-o` / `--output` can now point to a folder (an existing one, or a path ending with a slash, which is
  created): the screenshot is saved there under the default timestamped name.

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
