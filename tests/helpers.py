import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flipshot as f  # noqa: E402


class FakeSerial:
    """Scripted stand-in for serial.Serial.

    events is a list of:
      ("data", bytes)          bytes become readable
      ("delay", secs, bytes)   bytes arrive after secs; a read whose timeout is
                               shorter than secs returns b"" (a timeout)
      ("gap",)                 one read times out
    Reading past the end of the script always times out.
    """

    def __init__(self, events=(), timeout=0.2):
        self.events = list(events)
        self.timeout = timeout
        self.rts = False
        self.buf = b""
        self.written = []
        self.closed = False

    def read(self, n=1):
        if not self.buf:
            if not self.events:
                return b""
            event = self.events[0]
            if event[0] == "gap":
                self.events.pop(0)
                return b""
            if event[0] == "delay":
                if self.timeout is not None and self.timeout < event[1]:
                    return b""
                self.events.pop(0)
                self.buf = event[2]
            else:
                self.events.pop(0)
                self.buf = event[1]
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def close(self):
        self.closed = True


def frame_message(fill=0xFF, size=None):
    """A length-prefixed RPC message carrying a screen frame."""
    data = bytes([fill]) * (f.FRAME_BYTES if size is None else size)
    body = f.encode_length_delimited(
        f.FIELD_GUI_SCREEN_FRAME,
        f.encode_length_delimited(f.FIELD_SCREEN_FRAME_DATA, data),
    )
    return f.encode_varint(len(body)) + body


def device_info_message(key="hardware.name", value="Fliptest", has_next=False):
    inner = f.encode_length_delimited(f.FIELD_DEVICE_INFO_KEY, key.encode()) + (
        f.encode_length_delimited(f.FIELD_DEVICE_INFO_VALUE, value.encode())
    )
    body = f.encode_length_delimited(f.FIELD_SYSTEM_DEVICE_INFO_RESPONSE, inner)
    if has_next:
        body += f.encode_varint_field(f.FIELD_HAS_NEXT, 1)
    return f.encode_varint(len(body)) + body
