#!/usr/bin/env python3
"""
Grab one frame from a Flipper Zero's screen via its RPC protocol and save it
as a native-resolution (128x64) black & white PNG.

Install with:
    pip3 install flipshot
or from source:
    pip3 install pyserial

Use pip3 or python3 -m pip, not bare pip (on macOS that often targets Apple Python 3.9).
Homebrew Python may require break-system-packages in ~/.config/pip/pip.conf or on the command line.
If import serial fails after installing pyserial, run: pip3 uninstall serial

Usage:
    flipshot [-v] [-b [N] [M]] [serial_port] [output.png]

If serial_port is omitted, the script tries to auto-detect a connected Flipper.
If output.png is omitted, the file name is
flipshot-<device-name>-<YYYY-MM-DD--HH-MM-SS-MSS>.png

-b, --burst [N] [M] saves N screenshots, pausing M milliseconds between them.
N defaults to 10 (-1 keeps going until the script is stopped). M defaults to
1000 and must be between 100 and 5000.
"""

import argparse
import os
import re
import signal
import struct
import sys
import time
import zlib
from datetime import datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Optional, Tuple

import serial
import serial.tools.list_ports
from serial.tools.list_ports_common import ListPortInfo

SCREEN_W, SCREEN_H = 128, 64

# --- Flipper RPC field numbers (from flipperdevices/flipperzero-protobuf) ---
# Main message: command_id=1 (varint), command_status=2 (varint), has_next=3 (varint)
FIELD_COMMAND_ID = 1
FIELD_HAS_NEXT = 3
FIELD_GUI_START_SCREEN_STREAM = 20   # oneof: StartScreenStreamRequest (empty)
FIELD_GUI_STOP_SCREEN_STREAM = 21    # oneof: StopScreenStreamRequest (empty)
FIELD_GUI_SCREEN_FRAME = 22          # oneof: ScreenFrame { bytes data = 1; ... }
FIELD_SYSTEM_DEVICE_INFO_REQUEST = 32
FIELD_SYSTEM_DEVICE_INFO_RESPONSE = 33
FIELD_SCREEN_FRAME_DATA = 1
FIELD_DEVICE_INFO_KEY = 1
FIELD_DEVICE_INFO_VALUE = 2

DEVICE_NAME_INFO_KEYS = ("hardware.name", "hardware_name")

FLIPPER_USB_VID = 0x0483
FLIPPER_USB_PID = 0x5740

BURST_DEFAULT_COUNT = 10
BURST_DEFAULT_PAUSE_MS = 1000
BURST_MIN_PAUSE_MS = 100
BURST_MAX_PAUSE_MS = 5000
FRAME_BYTES = (SCREEN_W * SCREEN_H) // 8


# ---------------------------------------------------------------------------
# Minimal protobuf varint + wire-format helpers (no protobuf library needed)
# ---------------------------------------------------------------------------
def encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode_tag(field_number: int, wire_type: int) -> bytes:
    return encode_varint((field_number << 3) | wire_type)


def encode_length_delimited(field_number: int, payload: bytes) -> bytes:
    return encode_tag(field_number, 2) + encode_varint(len(payload)) + payload


def encode_varint_field(field_number: int, value: int) -> bytes:
    return encode_tag(field_number, 0) + encode_varint(value)


def read_varint(read_byte):
    """read_byte() must return the next single byte (int) from the stream."""
    result = 0
    shift = 0
    while True:
        b = read_byte()
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result
        shift += 7


def iter_fields(buf: bytes):
    """Yield (field_number, wire_type, value) for a flat protobuf message.
    value is an int for wire type 0/1/5, or raw bytes for wire type 2."""
    i = 0
    n = len(buf)
    while i < n:
        tag, shift = 0, 0
        while True:
            b = buf[i]
            i += 1
            tag |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        field_number = tag >> 3
        wire_type = tag & 0x7

        if wire_type == 0:  # varint
            value, shift = 0, 0
            while True:
                b = buf[i]
                i += 1
                value |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            yield field_number, wire_type, value
        elif wire_type == 1:  # 64-bit
            yield field_number, wire_type, buf[i:i + 8]
            i += 8
        elif wire_type == 2:  # length-delimited
            length, shift = 0, 0
            while True:
                b = buf[i]
                i += 1
                length |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            yield field_number, wire_type, buf[i:i + length]
            i += length
        elif wire_type == 5:  # 32-bit
            yield field_number, wire_type, buf[i:i + 4]
            i += 4
        else:
            raise ValueError(f"Unsupported wire type {wire_type}")


def find_field(buf: bytes, wanted_field_number: int):
    for field_number, _wire_type, value in iter_fields(buf):
        if field_number == wanted_field_number:
            return value
    return None


# ---------------------------------------------------------------------------
# Serial / RPC session handling
# ---------------------------------------------------------------------------
def sanitize_filename_component(value: str) -> str:
    cleaned = re.sub(r"[^\w\-.]+", "_", value.strip(), flags=re.ASCII)
    return cleaned or "unknown"


def default_output_path(device_name: str) -> str:
    now = datetime.now()
    timestamp = (
        now.strftime("%Y-%m-%d--%H-%M-%S")
        + f"-{now.microsecond // 1000:03d}"
    )
    safe_name = sanitize_filename_component(device_name)
    return f"flipshot-{safe_name}-{timestamp}.png"


def decode_string_field(buf: bytes, field_number: int) -> Optional[str]:
    raw = find_field(buf, field_number)
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace")


def read_frame_and_device_name(ser: serial.Serial, timeout: float) -> Tuple[Optional[bytes], str]:
    """Read screen-frame and device-info responses off the same stream, whichever
    arrives first. The two requests are independent, so there's no need to wait
    for one to fully finish before sending (and waiting on) the other."""
    deadline = time.time() + timeout
    frame_data = None
    device_name = None
    device_info_done = False
    while time.time() < deadline and (frame_data is None or not device_info_done):
        msg = read_message(ser)

        if frame_data is None:
            screen_frame = find_field(msg, FIELD_GUI_SCREEN_FRAME)
            if screen_frame is not None:
                data = find_field(screen_frame, FIELD_SCREEN_FRAME_DATA)
                if data:
                    frame_data = data
                continue

        info = find_field(msg, FIELD_SYSTEM_DEVICE_INFO_RESPONSE)
        if info is not None:
            key = decode_string_field(info, FIELD_DEVICE_INFO_KEY)
            value = decode_string_field(info, FIELD_DEVICE_INFO_VALUE)
            if key in DEVICE_NAME_INFO_KEYS and value:
                device_name = value

            has_next = find_field(msg, FIELD_HAS_NEXT)
            if has_next is None or has_next == 0:
                device_info_done = True

    return frame_data, (device_name or "unknown")


def is_flipper_port(port_info: ListPortInfo) -> bool:
    device = (port_info.device or "").lower()
    description = (port_info.description or "").lower()
    manufacturer = (port_info.manufacturer or "").lower()
    if "flip" in device or "usbmodemflip" in device:
        return True
    if "flipper" in description or "flip_" in description:
        return True
    if "flipper" in manufacturer:
        return True
    if port_info.vid == FLIPPER_USB_VID and port_info.pid == FLIPPER_USB_PID:
        return True
    return False


def find_flipper_port() -> Optional[str]:
    matches = [p for p in serial.tools.list_ports.comports() if is_flipper_port(p)]
    if not matches:
        return None
    for port_info in matches:
        if port_info.device.startswith("/dev/cu."):
            return port_info.device
    return matches[0].device


def quit_message(message: str, code: int = 1) -> None:
    print(message)
    sys.exit(code)


def _drain_until_idle(ser: serial.Serial, idle_gap: float = 0.05, max_wait: float = 0.5) -> None:
    """Read and discard bytes until the port has been quiet for idle_gap seconds,
    or max_wait total has elapsed (same worst case as a blind sleep, but returns
    as soon as the Flipper actually stops talking)."""
    original_timeout = ser.timeout
    ser.timeout = idle_gap
    try:
        deadline = time.time() + max_wait
        while time.time() < deadline:
            if not ser.read(4096):
                return
    finally:
        ser.timeout = original_timeout


def start_rpc_session(ser: serial.Serial) -> None:
    """Switch Flipper CLI from text mode to length-prefixed protobuf RPC."""
    ser.rts = True
    time.sleep(0.5)  # let the Flipper notice the RTS toggle; nothing to poll on yet
    ser.reset_input_buffer()
    ser.write(b"\r")
    _drain_until_idle(ser, max_wait=0.3)
    # Flipper expects CR only here; CRLF does not enter RPC mode reliably.
    ser.write(b"start_rpc_session\r")
    _drain_until_idle(ser, max_wait=0.5)
    ser.reset_input_buffer()


def write_message(ser: serial.Serial, body: bytes):
    ser.write(encode_varint(len(body)) + body)
    ser.flush()


def read_message(ser: serial.Serial) -> bytes:
    def read_byte():
        b = ser.read(1)
        if not b:
            raise TimeoutError("Serial read timed out waiting for RPC data")
        return b[0]

    length = read_varint(read_byte)
    data = b""
    while len(data) < length:
        chunk = ser.read(length - len(data))
        if not chunk:
            raise TimeoutError("Serial read timed out mid-message")
        data += chunk
    return data


def open_serial(port: str, timeout: float = 5.0) -> serial.Serial:
    return serial.Serial(
        port,
        baudrate=115200,
        timeout=timeout,
        dsrdtr=False,
        write_timeout=5,
    )


def send_start_screen_stream(ser: serial.Serial) -> None:
    request = encode_varint_field(FIELD_COMMAND_ID, 1) + encode_length_delimited(
        FIELD_GUI_START_SCREEN_STREAM, b""
    )
    write_message(ser, request)


def send_device_info_request(ser: serial.Serial) -> None:
    request = encode_varint_field(FIELD_COMMAND_ID, 10) + encode_length_delimited(
        FIELD_SYSTEM_DEVICE_INFO_REQUEST, b""
    )
    write_message(ser, request)


def send_stop_screen_stream(ser: serial.Serial) -> None:
    request = encode_varint_field(FIELD_COMMAND_ID, 2) + encode_length_delimited(
        FIELD_GUI_STOP_SCREEN_STREAM, b""
    )
    write_message(ser, request)


def frame_from_message(msg: bytes) -> Optional[bytes]:
    screen_frame = find_field(msg, FIELD_GUI_SCREEN_FRAME)
    if screen_frame is None:
        return None
    data = find_field(screen_frame, FIELD_SCREEN_FRAME_DATA)
    if not data or len(data) != FRAME_BYTES:
        return None
    return data


def require_frame(frame_data: Optional[bytes]) -> bytes:
    if frame_data is None:
        raise RuntimeError("Never received a screen frame before timing out")
    if len(frame_data) != FRAME_BYTES:
        raise RuntimeError(
            f"Unexpected frame size {len(frame_data)} bytes (expected {FRAME_BYTES})"
        )
    return frame_data


def grab_screen_frame(port: str, timeout: float = 5.0) -> Tuple[bytes, str]:
    ser = open_serial(port, timeout)
    try:
        start_rpc_session(ser)

        # Fire both requests up front -- they're independent, so we can read
        # whichever responses arrive first instead of waiting on them in series.
        send_start_screen_stream(ser)
        send_device_info_request(ser)

        try:
            frame_data, device_name = read_frame_and_device_name(ser, timeout)
        except TimeoutError:
            frame_data, device_name = None, "unknown"

        # Politely stop the stream regardless of success.
        send_stop_screen_stream(ser)
        return require_frame(frame_data), device_name
    finally:
        ser.close()


class BurstStopped(Exception):
    """The user asked an in-progress burst to end (Ctrl+C or SIGTERM)."""


class _StopFlag:
    def __init__(self) -> None:
        self.requested = False

    def request(self, _signum, _frame) -> None:
        self.requested = True


def wait_for_latest_frame(
    ser: serial.Serial,
    seconds: float,
    frame_data: bytes,
    stop: _StopFlag,
) -> bytes:
    """Keep reading for `seconds`, retaining the newest complete screen frame.

    The stream stays open during the pause. Discarding frames as they arrive
    avoids filling the serial buffer, and the frame returned is the screen at
    the end of the pause rather than one queued at the start.
    """
    deadline = time.time() + seconds
    original_timeout = ser.timeout
    try:
        while True:
            if stop.requested:
                raise BurstStopped
            remaining = deadline - time.time()
            if remaining <= 0:
                return frame_data
            ser.timeout = min(0.2, remaining)
            try:
                msg = read_message(ser)
            except TimeoutError:
                continue
            newer = frame_from_message(msg)
            if newer is not None:
                frame_data = newer
    finally:
        ser.timeout = original_timeout


def numbered_output_path(output: str, index: int) -> str:
    root, ext = os.path.splitext(output)
    if not ext:
        ext = ".png"
    return f"{root}-{index}{ext}"


def save_frame(frame_data: bytes, device_name: str, output: Optional[str], index: Optional[int]) -> str:
    if output is None:
        path = default_output_path(device_name)
    elif index is None:
        path = output
    else:
        path = numbered_output_path(output, index)
    save_png(path, frame_to_pixels(frame_data), SCREEN_W, SCREEN_H)
    return path


def _stopped_message(saved: int) -> str:
    if saved == 0:
        return "Stopped before any screenshot was saved."
    if saved == 1:
        return "Stopped after 1 screenshot."
    return f"Stopped after {saved} screenshots."


def capture_burst(
    port: str,
    count: int,
    pause_ms: int,
    output: Optional[str],
    timeout: float = 5.0,
) -> int:
    infinite = count < 0
    saved = 0
    ser = None
    streaming = False
    stop = _StopFlag()
    previous_term = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, stop.request)
    except (ValueError, OSError):
        previous_term = None

    try:
        ser = open_serial(port, timeout)
        start_rpc_session(ser)
        send_start_screen_stream(ser)
        streaming = True
        send_device_info_request(ser)
        try:
            frame_data, device_name = read_frame_and_device_name(ser, timeout)
        except TimeoutError:
            frame_data, device_name = None, "unknown"
        frame_data = require_frame(frame_data)

        while True:
            saved += 1
            path = save_frame(
                frame_data,
                device_name,
                output,
                saved if output is not None else None,
            )
            progress = f"{saved}" if infinite else f"{saved}/{count}"
            print(f"Saved native {SCREEN_W}x{SCREEN_H} PNG to {path} ({progress})")
            if stop.requested or (not infinite and saved >= count):
                break
            frame_data = wait_for_latest_frame(ser, pause_ms / 1000.0, frame_data, stop)
    except KeyboardInterrupt:
        print(f"\n{_stopped_message(saved)}")
        return 0
    except BurstStopped:
        print(_stopped_message(saved))
        return 0
    finally:
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
        if ser is not None:
            if streaming:
                try:
                    send_stop_screen_stream(ser)
                except (serial.SerialException, TimeoutError, OSError):
                    pass
            ser.close()

    if stop.requested:
        print(_stopped_message(saved))
    return 0


# ---------------------------------------------------------------------------
# Framebuffer -> PNG (no imaging library needed -- just stdlib zlib/struct)
# ---------------------------------------------------------------------------
def frame_to_pixels(frame_data: bytes) -> bytes:
    """Flipper's framebuffer is SSD1306-style page-addressed: 8 pages of 8 rows,
    128 columns, each byte = one column's 8 vertical pixels, LSB = topmost row.
    A set bit means "ink" (matches this project's own icon PNG convention:
    1 = black). Flip the 0x00/0xFF pair below if yours comes out inverted on
    your firmware version. Returns SCREEN_W*SCREEN_H grayscale bytes, row-major."""
    pixels = bytearray(b"\xff" * (SCREEN_W * SCREEN_H))  # 0xff = white background
    for i, byte in enumerate(frame_data):
        page = i // SCREEN_W
        col = i % SCREEN_W
        for bit in range(8):
            y = page * 8 + bit
            if y >= SCREEN_H:
                continue
            on = (byte >> bit) & 1
            pixels[y * SCREEN_W + col] = 0x00 if on else 0xFF
    return bytes(pixels)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def save_png(path: str, pixels: bytes, width: int, height: int) -> None:
    """Write an 8-bit grayscale PNG using only the standard library."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: None
        raw.extend(pixels[y * width:(y + 1) * width])
    idat = zlib.compress(bytes(raw), 9)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_png_chunk(b"IHDR", ihdr))
        f.write(_png_chunk(b"IDAT", idat))
        f.write(_png_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _version_string() -> str:
    try:
        return _pkg_version("flipshot")
    except PackageNotFoundError:
        return "0.0.0-dev"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flipshot",
        usage="%(prog)s [-h] [-v] [-b [N] [M]] [port] [output]",
        description="Grab a frame from a Flipper Zero's screen and save it as a PNG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  flipshot\n"
            "  flipshot -b\n"
            "  flipshot -b 20 250\n"
            "  flipshot --burst -1 100\n"
            "  flipshot /dev/cu.usbmodemflip_XXXX1 shot.png -b 5 500"
        ),
    )
    parser.add_argument(
        "port",
        nargs="?",
        default=None,
        help="Serial port to use (auto-detected if omitted), "
        "e.g. /dev/cu.usbmodemflip_XXXX1",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Output PNG path (default: flipshot-<device-name>-<timestamp>.png). "
        "With --burst and an explicit path, files are numbered: shot.png -> shot-1.png, shot-2.png, ...",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {_version_string()}",
    )
    # Values are parsed in _extract_burst_tokens. Registered here so -b/--burst
    # shows up in help; argparse would treat N=-1 as a flag and would also
    # swallow the port and output paths.
    parser.add_argument(
        "-b",
        "--burst",
        dest="_burst_flag",
        action="store_true",
        help=(
            "Capture N screenshots, pausing M milliseconds between them. "
            f"N defaults to {BURST_DEFAULT_COUNT}; -1 keeps going until the script is stopped. "
            f"M defaults to {BURST_DEFAULT_PAUSE_MS} and must be from "
            f"{BURST_MIN_PAUSE_MS} to {BURST_MAX_PAUSE_MS}."
        ),
    )
    return parser


def _is_int_token(token: str) -> bool:
    try:
        int(token, 10)
    except ValueError:
        return False
    return True


def _extract_burst_tokens(argv, parser):
    """Pull -b/--burst and up to two integer arguments out of argv.

    Returns (tokens or None, remaining argv). None means the flag was absent.
    Only integer tokens are consumed, so a port path after -b stays positional,
    and -1 is accepted as "run until stopped".
    """
    remaining = []
    tokens = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg not in ("-b", "--burst") and not arg.startswith("--burst="):
            remaining.append(arg)
            index += 1
            continue
        if tokens is not None:
            parser.error("-b/--burst can only be specified once")
        tokens = []
        if arg.startswith("--burst="):
            inline = arg.split("=", 1)[1]
            if inline != "":
                tokens.append(inline)
        index += 1
        while index < len(argv) and len(tokens) < 2 and _is_int_token(argv[index]):
            tokens.append(argv[index])
            index += 1
        if index < len(argv) and _is_int_token(argv[index]):
            parser.error("-b/--burst accepts at most 2 values: N and M")
    return tokens, remaining


def _validate_burst(tokens, parser):
    if tokens is None:
        return None
    count = BURST_DEFAULT_COUNT
    pause_ms = BURST_DEFAULT_PAUSE_MS
    if len(tokens) >= 1:
        try:
            count = int(tokens[0], 10)
        except ValueError:
            parser.error(f"N must be an integer number of screenshots, got {tokens[0]!r}")
    if len(tokens) >= 2:
        try:
            pause_ms = int(tokens[1], 10)
        except ValueError:
            parser.error(f"M must be an integer number of milliseconds, got {tokens[1]!r}")
    if count != -1 and count < 1:
        parser.error("N must be a positive number of screenshots, or -1 to keep going until stopped")
    if pause_ms < BURST_MIN_PAUSE_MS or pause_ms > BURST_MAX_PAUSE_MS:
        parser.error(
            f"M must be between {BURST_MIN_PAUSE_MS} and {BURST_MAX_PAUSE_MS} milliseconds"
        )
    return count, pause_ms


def parse_args(argv=None) -> argparse.Namespace:
    parser = _build_parser()
    if argv is None:
        argv = sys.argv[1:]
    burst_tokens, remaining = _extract_burst_tokens(list(argv), parser)
    args = parser.parse_args(remaining)
    args.burst = _validate_burst(burst_tokens, parser)
    del args._burst_flag
    return args


def capture_once(port: str, output: Optional[str]) -> int:
    frame_data, device_name = grab_screen_frame(port)
    out_path = output if output is not None else default_output_path(device_name)
    save_png(out_path, frame_to_pixels(frame_data), SCREEN_W, SCREEN_H)
    print(f"Saved native {SCREEN_W}x{SCREEN_H} PNG to {out_path}")
    return 0


def main() -> int:
    args = parse_args()

    port = args.port
    if port is None:
        port = find_flipper_port()
        if port is None:
            quit_message(
                "No Flipper Zero detected.\n"
                "Connect it via USB, then run this script again.\n"
                "Or pass the serial port explicitly, for example:\n"
                "  flipshot /dev/cu.usbmodemflip_YourName1"
            )

    if args.burst is not None:
        count, pause_ms = args.burst
        if count < 0:
            print(
                f"Capturing screenshots until stopped, {pause_ms} ms apart. "
                "Press Ctrl+C to stop."
            )
        else:
            print(f"Capturing {count} screenshots, {pause_ms} ms apart.")

    print(f"Connecting to {port} ...")
    try:
        if args.burst is None:
            return capture_once(port, args.output)
        count, pause_ms = args.burst
        return capture_burst(port, count, pause_ms, args.output)
    except serial.SerialException as exc:
        quit_message(
            f"Could not open {port}: {exc}\n"
            "Close qFlipper or any serial terminal using the Flipper, then retry."
        )
    except TimeoutError:
        quit_message(
            f"Timed out waiting for a response on {port}.\n"
            "Check the USB cable, wake the Flipper, and make sure nothing else is using the port."
        )
    except RuntimeError as exc:
        quit_message(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
