# flipshot

Grab one frame from a [Flipper Zero](https://flipperzero.one/)'s screen over USB serial and
save it as a native-resolution (128x64) black & white PNG (optionally scaled up and painted in
the Flipper's orange) - no qFlipper, no companion app,
just a USB cable and this script.

It's very good for manual UI prototyping and back-and-forth overpainting in apps like [Aseprite](https://store.steampowered.com/app/431730/Aseprite/), or as instant visual feedback so your AI can actually see your Flipper Zero's screen.

Here are some examples:

![](https://raw.githubusercontent.com/wiki/ManeFunction/flipshot/flipshot-1.png)&nbsp;&nbsp;![](https://raw.githubusercontent.com/wiki/ManeFunction/flipshot/flipshot-2.png)&nbsp;&nbsp;![](https://raw.githubusercontent.com/wiki/ManeFunction/flipshot/flipshot-3.png)&nbsp;&nbsp;![](https://raw.githubusercontent.com/wiki/ManeFunction/flipshot/flipshot-4.png)

<a href="https://github.com/ManeFunction/clock-o-dial--fz"><img src="https://raw.githubusercontent.com/wiki/ManeFunction/flipshot/flipshot-big.png" alt="clock-o-dial"></a>

## Installation

**flipshot** is available from a variety of sources.
`pip` or `brew` is recommended, because they have a convenient way to manage updates automatically.

1) **pip (Recommended for everyone with a Python environment)**
    - You can check if you have Python installed by running `python --version` in the Terminal or cmd.
    - For Mac and Linux users, there is a high chance that you already have Python installed on your system.
    - For Windows users, you can download Python from the [official website](https://www.python.org/downloads/).
    - After confirmation, install **flipshot** through the [PyPI](https://pypi.org/project/flipshot) package
      manager, typing `pip install flipshot` in the Terminal. For Mac users, you may need to use `pip3` instead
      of `pip`.
    - Verify the installation with `flipshot --version`.
    - You are perfect, you can use the app with `flipshot` from any folder in your system.
2) **brew (Recommended for Mac and Linux users)**
    - Type `brew tap manefunction/tap` in your Terminal to add my custom tap (app source) to your brew sources,
      if you haven't already.
    - Type `brew install flipshot` to install the application itself.
    - Verify the installation with `flipshot --version`.
    - You are perfect, you can use the app with `flipshot` from any folder in your system.
3) **Python package (manual installation, for advanced users)**
    - Clone the repository or download the source code from GitHub.
    - Go to the folder with the script in your Terminal.
    - Run `pip install .` to install `flipshot` to your system.
    - Run `flipshot --version` to verify the script is working.
4) **Ready-to-use Windows binary (for Windows users without Python)**
    - Download `flipshot.exe` from the [GitHub Releases](https://github.com/ManeFunction/flipshot/releases) page.
    - Since it isn't code-signed, Windows SmartScreen may warn about an unrecognized publisher the first time
      you run it — click "More info" → "Run anyway" to proceed.
    - Open Command Prompt or PowerShell, `cd` to the folder with `flipshot.exe`, and run `.\flipshot.exe --version`
      to verify it works.
    - You are perfect, you can use the app with `.\flipshot.exe`.
5) **Python script (manual usage, for advanced users)**
    - If you are familiar with Python scripts, venv, and dependencies, you can simply clone the repository,
      `pip3 install pyserial`, and run `src/flipshot.py` directly. Feel free to modify the script for yourself.


## Usage

```
flipshot [-h] [-v] [-b [N] [M]] [-s xN] [-p] [-o OUTPUT] [serial_port]
```

- `-h`, `--help` — show the help text and exit.
- `-v`, `--version` — print the version and exit.
- `serial_port` is optional — flipshot auto-detects a connected Flipper Zero over USB.
  Pass it explicitly if auto-detection fails, e.g. `flipshot /dev/cu.usbmodemflip_XXXX1`.
- `-o`, `--output` is optional — defaults to `flipshot-<device-name>-<YYYY-MM-DD--HH-MM-SS-MSS>.png`
  in the current folder. If it points to a folder (an existing one, or a path ending with a slash, which
  is created), the screenshot is saved there under the default name: `flipshot -o ~/Pictures/flipper/`.
  With `--burst` and an explicit file path, files are numbered: `shot.png` becomes `shot-1.png`,
  `shot-2.png`, and so on; with a folder, every file gets its own timestamped default name.
- `-b`, `--burst [N] [M]` — take N screenshots, pausing M milliseconds between them.
  N defaults to 10. `-1` keeps capturing until you stop the script (Ctrl+C).
  M defaults to 1000 and must be between 100 and 5000.
- `-s`, `--scale xN` — enlarge the image N times (N from 1 to 10), so every Flipper pixel becomes
  an NxN block: `-s x3` gives a 384x192 image. Defaults to `x1` (native 128x64).
- `-p`, `--paint` — use the Flipper's own colors: the white background becomes orange (`#fe8a2c`).
  The image is saved as a 2-color indexed PNG instead of grayscale.

```
flipshot -b
flipshot -b 20 250
flipshot --burst -1 100
flipshot -s x4 -p    // the same format (visually) qFlipper do
```

Close qFlipper or any other serial terminal connected to the Flipper before running flipshot —
only one process can hold the serial port at a time.


## PNG format

The Flipper's display has only two states per pixel (ink and background), so flipshot stores one bit per
pixel, and the PNG type depends on the options:

| Options | PNG type | Colors | Notes |
|---|---|---|---|
| *(default)* | 1-bit grayscale | black and white | No palette; the single bit is the shade itself (0 = black, 1 = white). |
| `-p` | 1-bit indexed (2-color palette) | black and orange `#fe8a2c` | Still 1 bit per pixel; a tiny `PLTE` chunk maps bit 0 to black and bit 1 to orange. |

- **Compared to qFlipper:** qFlipper saves screenshots in the RGB color space, which spends 24 bits on every
  pixel. flipshot's files use 1 bit per pixel, so they are much lighter.
- **Compatibility:** both variants are standard PNGs and open in any viewer or editor. Programs that don't
  like 1-bit images can convert them to RGB in one step.


## How it works

flipshot switches the Flipper's CLI into its length-prefixed protobuf RPC mode over the same
USB-serial connection qFlipper uses, requests a screen-stream frame and the device's hardware
name, and encodes the resulting 1-bit framebuffer as a PNG — all with the Python standard
library plus [pyserial](https://pypi.org/project/pyserial/); no image library required.


## Repository info

This repo follows the [Conventional Commits](https://www.conventionalcommits.org/) specification.

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/ManeFunction/flipshot)](https://github.com/ManeFunction/flipshot/releases/latest)
[![GitHub All Releases](https://img.shields.io/github/downloads/ManeFunction/flipshot/total)](https://github.com/ManeFunction/flipshot/releases)
[![PyPI version](https://img.shields.io/pypi/v/flipshot)](https://pypi.org/project/flipshot/)
[![PyPI downloads](https://img.shields.io/pypi/dm/flipshot)](https://pypi.org/project/flipshot/)
[![GitHub Sponsors](https://img.shields.io/github/sponsors/ManeFunction?label=Sponsor&logo=GitHubSponsors&style=flat)](https://github.com/sponsors/ManeFunction)
