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
    flipshot [-v] [-b [N] [M]] [-s X] [-p] [-o output.png] [serial_port]

If serial_port is omitted, the script tries to auto-detect a connected Flipper.
If -o/--output is omitted, the file name is
flipshot-<device-name>-<YYYY-MM-DD--HH-MM-SS-MSS>.png
If -o/--output is a folder (an existing directory, or a path ending with a
slash, which is created), the file is saved there under that default name.

-b [N] [M], --burst [N] [M] saves N screenshots, pausing M milliseconds between them.
N defaults to 10 (-1 keeps going until the script is stopped). M defaults to
1000 and must be between 100 and 5000.

-s X, --scale X enlarges the image X times (X from 1 to 10), so every Flipper
pixel becomes an XxX block, e.g. -s 3.
-p, --paint draws the screen in Flipper's own colors (orange background
instead of white).
"""

import argparse
import os
import re
import signal
import struct
import sys
import time
import zlib
from contextlib import contextmanager
from datetime import datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import NamedTuple, NoReturn, Optional, Tuple

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

# Main.command_id values for the requests we send.
COMMAND_START_SCREEN_STREAM = 1
COMMAND_STOP_SCREEN_STREAM = 2
COMMAND_DEVICE_INFO = 10

# Screen frames are ~1 KB; anything this large means we are reading garbage.
MAX_MESSAGE_BYTES = 1024 * 1024

DEVICE_NAME_INFO_KEYS = ("hardware.name", "hardware_name")

FLIPPER_USB_VID = 0x0483
FLIPPER_USB_PID = 0x5740

BURST_DEFAULT_COUNT = 10
BURST_DEFAULT_PAUSE_MS = 1000
BURST_MIN_PAUSE_MS = 100
BURST_MAX_PAUSE_MS = 5000
FRAME_BYTES = (SCREEN_W * SCREEN_H) // 8

SCALE_MIN = 1
SCALE_MAX = 10

# Flipper's LCD: black pixels on an orange backlight.
PAINT_INK = (0x00, 0x00, 0x00)
PAINT_BACKGROUND = (0xFE, 0x8A, 0x2C)


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
        if shift > 63:
            raise ValueError("Protobuf varint is too long")


def decode_varint(buf: bytes, i: int) -> Tuple[int, int]:
    """Decode the varint at buf[i]. Returns (value, index after it)."""
    pos = i

    def read_byte():
        nonlocal pos
        if pos >= len(buf):
            raise ValueError("Truncated protobuf message")
        pos += 1
        return buf[pos - 1]

    return read_varint(read_byte), pos


def _take(buf: bytes, i: int, count: int) -> Tuple[bytes, int]:
    if i + count > len(buf):
        raise ValueError("Truncated protobuf message")
    return buf[i:i + count], i + count


def iter_fields(buf: bytes):
    """Yield (field_number, wire_type, value) for a flat protobuf message.
    value is an int for wire type 0, or raw bytes for wire types 1, 2 and 5.
    Raises ValueError on truncated or unsupported input."""
    i = 0
    while i < len(buf):
        tag, i = decode_varint(buf, i)
        field_number = tag >> 3
        wire_type = tag & 0x7

        if wire_type == 0:  # varint
            value, i = decode_varint(buf, i)
        elif wire_type == 1:  # 64-bit
            value, i = _take(buf, i, 8)
        elif wire_type == 2:  # length-delimited
            length, i = decode_varint(buf, i)
            value, i = _take(buf, i, length)
        elif wire_type == 5:  # 32-bit
            value, i = _take(buf, i, 4)
        else:
            raise ValueError(f"Unsupported wire type {wire_type}")
        yield field_number, wire_type, value


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


class FrameEvent(NamedTuple):
    """A screen frame from the Flipper. data is not checked for length."""

    data: bytes


class DeviceInfoEvent(NamedTuple):
    """One key/value entry of the device info response; has_next is False on the last."""

    key: Optional[str]
    value: Optional[str]
    has_next: bool


def decode_event(msg: bytes):
    """Decode a raw RPC message into a FrameEvent or DeviceInfoEvent, or None for
    anything else. Raises ValueError if the message is malformed."""
    screen_frame = find_field(msg, FIELD_GUI_SCREEN_FRAME)
    if screen_frame is not None:
        data = find_field(screen_frame, FIELD_SCREEN_FRAME_DATA)
        return FrameEvent(data) if data else None

    info = find_field(msg, FIELD_SYSTEM_DEVICE_INFO_RESPONSE)
    if info is not None:
        return DeviceInfoEvent(
            decode_string_field(info, FIELD_DEVICE_INFO_KEY),
            decode_string_field(info, FIELD_DEVICE_INFO_VALUE),
            find_field(msg, FIELD_HAS_NEXT) not in (None, 0),
        )
    return None


def read_frame_and_device_name(ser: serial.Serial, timeout: float) -> Tuple[Optional[bytes], str]:
    """Read screen-frame and device-info responses off the same stream, whichever
    arrives first. The two requests are independent, so there's no need to wait
    for one to fully finish before sending (and waiting on) the other."""
    deadline = time.time() + timeout
    frame_data = None
    device_name = None
    device_info_done = False
    while time.time() < deadline and (frame_data is None or not device_info_done):
        event = decode_event(read_message(ser))

        if isinstance(event, FrameEvent):
            if frame_data is None:
                frame_data = event.data
        elif isinstance(event, DeviceInfoEvent):
            if event.key in DEVICE_NAME_INFO_KEYS and event.value:
                device_name = event.value
            if not event.has_next:
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


def quit_message(message: str, code: int = 1) -> NoReturn:
    print(message, file=sys.stderr)
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


def read_message(ser: serial.Serial, body_timeout: float = 2.0) -> bytes:
    """Read one length-prefixed RPC message.

    A timeout before the first byte is harmless (TimeoutError, nothing consumed).
    Once the message has started, the rest gets at least body_timeout seconds,
    and running out of time then is an error: carrying on would leave a half-read
    message in the buffer and misread everything after it.
    """
    first = ser.read(1)
    if not first:
        raise TimeoutError("Serial read timed out waiting for RPC data")

    original_timeout = ser.timeout
    if original_timeout is not None and original_timeout < body_timeout:
        ser.timeout = body_timeout
    try:
        pending = [first[0]]

        def read_byte():
            if pending:
                return pending.pop()
            b = ser.read(1)
            if not b:
                raise RuntimeError("Serial read timed out mid-message; stream is out of sync")
            return b[0]

        length = read_varint(read_byte)
        if length > MAX_MESSAGE_BYTES:
            raise RuntimeError(f"Implausible RPC message length {length}; stream is out of sync")
        data = b""
        while len(data) < length:
            chunk = ser.read(length - len(data))
            if not chunk:
                raise RuntimeError("Serial read timed out mid-message; stream is out of sync")
            data += chunk
        return data
    finally:
        ser.timeout = original_timeout


def open_serial(port: str, timeout: float = 5.0) -> serial.Serial:
    try:
        return serial.Serial(
            port,
            baudrate=115200,
            timeout=timeout,
            dsrdtr=False,
            write_timeout=5,
        )
    except serial.SerialException as exc:
        raise RuntimeError(
            f"Could not open {port}: {exc}\n"
            "Close qFlipper or any serial terminal using the Flipper, then retry."
        ) from exc


def _empty_request(command_id: int, field_number: int) -> bytes:
    return encode_varint_field(FIELD_COMMAND_ID, command_id) + encode_length_delimited(
        field_number, b""
    )


def send_start_screen_stream(ser: serial.Serial) -> None:
    write_message(
        ser, _empty_request(COMMAND_START_SCREEN_STREAM, FIELD_GUI_START_SCREEN_STREAM)
    )


def send_device_info_request(ser: serial.Serial) -> None:
    write_message(ser, _empty_request(COMMAND_DEVICE_INFO, FIELD_SYSTEM_DEVICE_INFO_REQUEST))


def send_stop_screen_stream(ser: serial.Serial) -> None:
    write_message(
        ser, _empty_request(COMMAND_STOP_SCREEN_STREAM, FIELD_GUI_STOP_SCREEN_STREAM)
    )


def require_frame(frame_data: Optional[bytes]) -> bytes:
    if frame_data is None:
        raise RuntimeError(
            "Never received a screen frame before timing out.\n"
            "Check the USB cable, wake the Flipper, and make sure nothing else is using the port."
        )
    if len(frame_data) != FRAME_BYTES:
        raise RuntimeError(
            f"Unexpected frame size {len(frame_data)} bytes (expected {FRAME_BYTES})"
        )
    return frame_data


def connect_rpc(port: str, timeout: float = 5.0) -> serial.Serial:
    """Open the port and switch the Flipper into RPC mode. This is the one place
    that touches real hardware; tests substitute a fake with the same signature."""
    ser = open_serial(port, timeout)
    try:
        start_rpc_session(ser)
    except BaseException:
        ser.close()
        raise
    return ser


class ScreenStream:
    """A running screen stream: the device name and the newest frame seen so far."""

    def __init__(self, ser: serial.Serial, device_name: str, frame: bytes) -> None:
        self._ser = ser
        self.device_name = device_name
        self.frame = frame

    def wait_for_latest(self, seconds: float) -> bytes:
        """Keep reading for `seconds`, retaining the newest complete screen frame.

        The stream stays open during the pause. Discarding frames as they arrive
        avoids filling the serial buffer, and the frame returned is the screen at
        the end of the pause rather than one queued at the start.
        """
        ser = self._ser
        deadline = time.time() + seconds
        original_timeout = ser.timeout
        try:
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return self.frame
                ser.timeout = min(0.2, remaining)
                try:
                    msg = read_message(ser)
                except TimeoutError:
                    continue
                event = decode_event(msg)
                if isinstance(event, FrameEvent) and len(event.data) == FRAME_BYTES:
                    self.frame = event.data
        finally:
            ser.timeout = original_timeout


@contextmanager
def flipper_stream(port: str, timeout: float = 5.0, connect=connect_rpc):
    """Connect, start the screen stream, and yield a ScreenStream holding the first
    frame. On exit the stream is stopped (errors while stopping are ignored so
    they never mask the real one) and the port closed."""
    ser = connect(port, timeout)
    streaming = False
    try:
        # Fire both requests up front -- they're independent, so we can read
        # whichever responses arrive first instead of waiting on them in series.
        send_start_screen_stream(ser)
        streaming = True
        send_device_info_request(ser)

        try:
            frame_data, device_name = read_frame_and_device_name(ser, timeout)
        except TimeoutError:
            frame_data, device_name = None, "unknown"
        yield ScreenStream(ser, device_name, require_frame(frame_data))
    finally:
        if streaming:
            try:
                send_stop_screen_stream(ser)
            except (serial.SerialException, TimeoutError, OSError):
                pass
        ser.close()


def grab_screen_frame(
    port: str, timeout: float = 5.0, connect=connect_rpc
) -> Tuple[bytes, str]:
    with flipper_stream(port, timeout, connect) as stream:
        return stream.frame, stream.device_name


@contextmanager
def sigterm_as_interrupt():
    """Treat SIGTERM like Ctrl+C for the duration of the block."""

    def handler(_signum, _frame):
        raise KeyboardInterrupt

    try:
        previous = signal.signal(signal.SIGTERM, handler)
    except (ValueError, OSError):  # not the main thread, or no SIGTERM here
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous if previous is not None else signal.SIG_DFL)


def numbered_output_path(output: str, index: int) -> str:
    root, ext = os.path.splitext(output)
    if not ext:
        ext = ".png"
    return f"{root}-{index}{ext}"


def is_output_folder(output: Optional[str]) -> bool:
    """True if -o names a folder rather than a file: an existing directory, or
    any path written with a trailing separator (which is created on demand)."""
    if output is None:
        return False
    return output.endswith(("/", os.sep)) or os.path.isdir(output)


class Destination:
    """Where screenshots are saved: the -o argument, resolved once.

    No -o gives a timestamped file in the current folder; a folder (see
    is_output_folder) gives timestamped files inside it, created on demand; a
    file path is used as is, or numbered (shot-1.png, shot-2.png, ...) when
    numbered is true, as it is for a burst.
    """

    def __init__(self, output: Optional[str], numbered: bool = False) -> None:
        self._output = output
        self._folder = is_output_folder(output)
        self._numbered = numbered and output is not None and not self._folder

    def path_for(self, device_name: str, index: int = 1) -> str:
        """The path for screenshot number `index` (only used when numbering)."""
        if self._folder:
            os.makedirs(self._output, exist_ok=True)
            return os.path.join(self._output, default_output_path(device_name))
        if self._output is None:
            return default_output_path(device_name)
        if self._numbered:
            return numbered_output_path(self._output, index)
        return self._output


def save_frame(frame_data: bytes, path: str, scale: int = 1, paint: bool = False) -> None:
    save_png(path, frame_to_pixels(frame_data), SCREEN_W, SCREEN_H, scale, paint)


class CaptureRequest(NamedTuple):
    """What the user asked for, minus how many (see Burst)."""

    port: str
    output: Optional[str]
    scale: int = 1
    paint: bool = False


class Burst(NamedTuple):
    count: int  # -1 keeps going until stopped
    pause_ms: int


def saved_message(path: str, scale: int) -> str:
    if scale == 1:
        return f"Saved native {SCREEN_W}x{SCREEN_H} PNG to {path}"
    return f"Saved {SCREEN_W * scale}x{SCREEN_H * scale} PNG (x{scale}) to {path}"


def _stopped_message(saved: int) -> str:
    if saved == 0:
        return "Stopped before any screenshot was saved."
    if saved == 1:
        return "Stopped after 1 screenshot."
    return f"Stopped after {saved} screenshots."


def capture_burst(
    request: CaptureRequest,
    burst: Burst,
    timeout: float = 5.0,
    connect=connect_rpc,
) -> int:
    infinite = burst.count < 0
    destination = Destination(request.output, numbered=True)
    saved = 0
    try:
        with sigterm_as_interrupt(), flipper_stream(
            request.port, timeout, connect
        ) as stream:
            while True:
                saved += 1
                path = destination.path_for(stream.device_name, saved)
                save_frame(stream.frame, path, request.scale, request.paint)
                progress = f"{saved}" if infinite else f"{saved}/{burst.count}"
                print(f"{saved_message(path, request.scale)} ({progress})")
                if not infinite and saved >= burst.count:
                    break
                stream.wait_for_latest(burst.pause_ms / 1000.0)
    except KeyboardInterrupt:
        print(f"\n{_stopped_message(saved)}")
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


def save_png(
    path: str,
    pixels: bytes,
    width: int,
    height: int,
    scale: int = 1,
    paint: bool = False,
) -> None:
    """Write a 1-bit PNG using only the standard library. Every pixel here is
    already pure black (0x00) or white (0xff), so bit depth 1 is both smaller
    and more correct than 8-bit grayscale.

    scale enlarges the image so each source pixel becomes a scale x scale block.
    paint switches from 1-bit grayscale to a 2-color indexed palette (black ink
    on Flipper's orange background), which costs one extra PLTE chunk but keeps
    the same 1-bit packing."""
    out_w = width * scale
    out_h = height * scale
    if paint:
        color_type = 3  # indexed; palette index 0 = ink, 1 = background
    else:
        color_type = 0  # grayscale; sample 0 = black, 1 = white
    ihdr = struct.pack(">IIBBBBB", out_w, out_h, 1, color_type, 0, 0, 0)

    row_bytes = (out_w + 7) // 8
    raw = bytearray()
    for y in range(height):
        row = bytearray(row_bytes)
        for x in range(width):
            if pixels[y * width + x] != 0:  # non-zero (0xff) = white = bit 1
                for sx in range(x * scale, (x + 1) * scale):
                    row[sx // 8] |= 0x80 >> (sx % 8)
        line = b"\x00" + bytes(row)  # filter type: None
        raw.extend(line * scale)
    idat = zlib.compress(bytes(raw), 9)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_png_chunk(b"IHDR", ihdr))
        if paint:
            f.write(_png_chunk(b"PLTE", bytes(PAINT_INK + PAINT_BACKGROUND)))
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


def _parse_scale(value: str) -> int:
    match = re.fullmatch(r"[xX]?(\d+)", value.strip())
    if match is None:
        raise argparse.ArgumentTypeError(f"expected a whole number, e.g. 3, got {value!r}")
    scale = int(match.group(1))
    if scale < SCALE_MIN or scale > SCALE_MAX:
        raise argparse.ArgumentTypeError(
            f"N must be between {SCALE_MIN} and {SCALE_MAX}, got {scale}"
        )
    return scale


class _BurstPlaceholder(argparse.Action):
    """-b/--burst is pulled out of argv by _extract_burst_tokens before argparse
    runs, so reaching this means it was written in a form we don't understand
    (glued to other short flags, say). Fail loudly instead of ignoring it."""

    def __call__(self, parser, namespace, values, option_string=None):
        parser.error(
            "-b/--burst must be a separate argument, e.g. -b 5 500 "
            "(not combined with other short flags)"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flipshot",
        allow_abbrev=False,
        usage="%(prog)s [-h] [-v] [-b [N] [M]] [-s X] [-p] [-o OUTPUT] [port]",
        description="Grab a frame from a Flipper Zero's screen and save it as a PNG.",
        formatter_class=lambda prog: argparse.RawDescriptionHelpFormatter(
            prog, max_help_position=30
        ),
        epilog=(
            "examples:\n"
            "  flipshot\n"
            "  flipshot -b\n"
            "  flipshot -b 20 250\n"
            "  flipshot --burst -1 100\n"
            "  flipshot -o shot.png\n"
            "  flipshot -o ~/Pictures/flipper/\n"
            "  flipshot -s 4 -p\n"
            "  flipshot /dev/cu.usbmodemflip_XXXX1 -o shot.png -b 5 500 -s 3"
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
        "-o",
        "--output",
        default=None,
        metavar="OUTPUT",
        help="Output PNG path (default: flipshot-<device-name>-<timestamp>.png), "
        "or a folder to save the default-named file into (created if the path ends with a slash). "
        "With --burst and an explicit file path, files are numbered: shot.png -> shot-1.png, shot-2.png, ...",
    )
    parser.add_argument(
        "-s",
        "--scale",
        type=_parse_scale,
        default=1,
        metavar="X",
        help=f"Enlarge the image X times, X from {SCALE_MIN} to {SCALE_MAX}: "
        "every Flipper pixel becomes an XxX block, e.g. -s 3 gives 384x192 (default: 1)",
    )
    parser.add_argument(
        "-p",
        "--paint",
        action="store_true",
        help="Use Flipper's own colors: orange (#fe8a2c) background instead of white",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {_version_string()}",
    )
    # Values are parsed in _extract_burst_tokens. Registered here so -b/--burst
    # shows up in help; argparse would treat N=-1 as a flag and would also
    # swallow the port and output paths. argparse never sees a well-formed -b,
    # so the action only shapes the help entry and rejects malformed ones.
    parser.add_argument(
        "-b",
        "--burst",
        action=_BurstPlaceholder,
        default=argparse.SUPPRESS,
        metavar="[N] [M]",
        help=(
            "Capture N screenshots, pausing M milliseconds between them. "
            f"N defaults to {BURST_DEFAULT_COUNT}; -1 keeps going until the script is stopped. "
            f"M defaults to {BURST_DEFAULT_PAUSE_MS} and must be from "
            f"{BURST_MIN_PAUSE_MS} to {BURST_MAX_PAUSE_MS}."
        ),
    )
    return parser


_ATTACHED_BURST_RE = re.compile(r"-b(-?\d+)")


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
        attached = _ATTACHED_BURST_RE.fullmatch(arg)  # -b5, -b-1
        if (
            arg not in ("-b", "--burst")
            and not arg.startswith("--burst=")
            and attached is None
        ):
            remaining.append(arg)
            index += 1
            continue
        if tokens is not None:
            parser.error("-b/--burst can only be specified once")
        tokens = []
        if attached is not None:
            tokens.append(attached.group(1))
        elif arg.startswith("--burst="):
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
    return Burst(count, pause_ms)


def parse_args(argv=None) -> argparse.Namespace:
    parser = _build_parser()
    if argv is None:
        argv = sys.argv[1:]
    burst_tokens, remaining = _extract_burst_tokens(list(argv), parser)
    args = parser.parse_args(remaining)
    args.burst = _validate_burst(burst_tokens, parser)
    return args


def capture_once(request: CaptureRequest, connect=connect_rpc) -> int:
    frame_data, device_name = grab_screen_frame(request.port, connect=connect)
    path = Destination(request.output).path_for(device_name)
    save_frame(frame_data, path, request.scale, request.paint)
    print(saved_message(path, request.scale))
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
        request = CaptureRequest(port, args.output, args.scale, args.paint)
        if args.burst is None:
            return capture_once(request)
        return capture_burst(request, args.burst)
    except KeyboardInterrupt:
        quit_message("Cancelled.", 130)
    except RuntimeError as exc:
        quit_message(str(exc))
    except ValueError as exc:
        quit_message(f"Received a malformed message from the Flipper: {exc}")
    except serial.SerialException as exc:
        quit_message(f"Lost communication with {port}: {exc}")
    except OSError as exc:
        quit_message(f"Could not save the screenshot: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
