import unittest
from unittest import mock

from helpers import FakeSerial, device_info_message, f, frame_message


def jumping_clock():
    """A wall clock that leaps an hour forward after its first reading, as an NTP
    step or a laptop waking from sleep can."""
    calls = []

    def now():
        calls.append(1)
        return 1_000_000.0 + (3600.0 if len(calls) > 1 else 0.0)

    return now


class DeadlineClockTests(unittest.TestCase):
    """Deadlines must not follow the wall clock, which can jump."""

    def test_first_frame_wait_survives_a_clock_jump(self):
        ser = FakeSerial([("data", device_info_message()), ("data", frame_message(7))])
        with mock.patch.object(f.time, "time", jumping_clock()):
            frame, name = f.read_frame_and_device_name(ser, 5.0)
        self.assertEqual(name, "Fliptest")
        self.assertEqual(frame, b"\x07" * f.FRAME_BYTES)

    def test_burst_pause_survives_a_clock_jump(self):
        ser = FakeSerial([("data", frame_message(9))], timeout=0.05)
        stream = f.ScreenStream(ser, "x", b"\x00" * f.FRAME_BYTES)
        with mock.patch.object(f.time, "time", jumping_clock()):
            self.assertEqual(stream.wait_for_latest(0.2), b"\x09" * f.FRAME_BYTES)

    def test_drain_survives_a_clock_jump(self):
        ser = FakeSerial([("data", b"junk"), ("data", b"more")])
        with mock.patch.object(f.time, "time", jumping_clock()):
            f._drain_until_idle(ser)
        self.assertEqual(ser.events, [])  # everything was read before it went idle


if __name__ == "__main__":
    unittest.main()
