# flipshot

Grab one frame from a [Flipper Zero](https://flipperzero.one/)'s screen over USB serial and
save it as a native-resolution (128x64) black & white PNG — no qFlipper, no companion app,
just a serial cable and this script.


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
    - You are perfect, you can use the app with `flipshot [port] [output.png]` from any folder in your system.
2) **brew (Recommended for Mac and Linux users)**
    - Type `brew tap manefunction/tap` in your Terminal to add my custom tap (app source) to your brew sources,
      if you haven't already.
    - Type `brew install flipshot` to install the application itself.
    - Verify the installation with `flipshot --version`.
    - You are perfect, you can use the app with `flipshot [port] [output.png]` from any folder in your system.
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
    - You are perfect, you can use the app with `.\flipshot.exe [port] [output.png]`.
5) **Python script (manual usage, for advanced users)**
    - If you are familiar with Python scripts, venv, and dependencies, you can simply clone the repository,
      `pip3 install pyserial`, and run `src/flipshot.py` directly. Feel free to modify the script for yourself.


## Usage

```
flipshot [serial_port] [output.png]
```

- `serial_port` is optional — flipshot auto-detects a connected Flipper Zero over USB.
  Pass it explicitly if auto-detection fails, e.g. `flipshot /dev/cu.usbmodemflip_XXXX1`.
- `output.png` is optional — defaults to `flipshot-<device-name>-<YYYY-MM-DD--HH-MM-SS-MSS>.png`
  in the current folder.

Close qFlipper or any other serial terminal connected to the Flipper before running flipshot —
only one process can hold the serial port at a time.


## How it works

flipshot switches the Flipper's CLI into its length-prefixed protobuf RPC mode over the same
USB-serial connection qFlipper uses, requests a screen-stream frame and the device's hardware
name, and encodes the resulting 1-bit framebuffer as a PNG — all with the Python standard
library plus [pyserial](https://pypi.org/project/pyserial/); no image library required.


## Repository info

This repo follows the [Conventional Commits](https://www.conventionalcommits.org/) specification.
