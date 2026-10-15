import contextlib
import io
import os
import signal
import sys
import tempfile
import threading
import unittest

import serial

from helpers import FakeSerial, device_info_message, f, frame_message


def script(*messages, timeout=0.05):
    return FakeSerial([("data", m) for m in messages], timeout=timeout)


def connector(ser):
    return lambda port, timeout=5.0: ser


GOOD = (device_info_message(value="Fliptest"), frame_message(0xFF))


class FlipperStreamTests(unittest.TestCase):
    def test_grab_returns_frame_and_name_then_stops_and_closes(self):
        ser = script(*GOOD)
        frame, name = f.grab_screen_frame("x", connect=connector(ser))
        self.assertEqual(name, "Fliptest")
        self.assertEqual(len(frame.data), f.FRAME_BYTES)
        self.assertEqual(len(ser.written), 3)  # start stream, device info, stop stream
        self.assertTrue(ser.closed)

    def test_failed_stop_does_not_lose_the_frame(self):
        ser = script(*GOOD)
        calls = []

        def write(data):
            calls.append(data)
            if len(calls) == 3:
                raise serial.SerialException("unplugged")

        ser.write = write
        frame, _name = f.grab_screen_frame("x", connect=connector(ser))
        self.assertEqual(len(frame.data), f.FRAME_BYTES)
        self.assertTrue(ser.closed)

    def test_no_frame_is_an_error_and_cleans_up(self):
        ser = script(device_info_message())
        with self.assertRaisesRegex(RuntimeError, "Never received a screen frame"):
            f.grab_screen_frame("x", timeout=0.1, connect=connector(ser))
        self.assertTrue(ser.closed)
        self.assertEqual(len(ser.written), 3)  # the stream is still stopped

    def test_connect_failure_propagates(self):
        def connect(port, timeout=5.0):
            raise RuntimeError("Could not open")

        with self.assertRaisesRegex(RuntimeError, "Could not open"):
            f.grab_screen_frame("x", connect=connect)

    def test_error_inside_the_block_still_stops_and_closes(self):
        ser = script(*GOOD)
        with self.assertRaises(ZeroDivisionError):
            with f.flipper_stream("x", connect=connector(ser)):
                1 / 0
        self.assertTrue(ser.closed)
        self.assertEqual(len(ser.written), 3)


class OrientationCaptureTests(unittest.TestCase):
    def test_left_handed_capture_is_saved_upright(self):
        ser = script(
            device_info_message(),
            frame_message(0xFF, orientation=f.ORIENTATION_HORIZONTAL_FLIP),
        )
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "shot.png")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                f.capture_once(f.CaptureRequest("x", path), connect=connector(ser))
            with open(path, "rb") as fh:
                expected = f.render_png(
                    f.Frame(b"\xff" * f.FRAME_BYTES, f.ORIENTATION_HORIZONTAL_FLIP)
                )
                self.assertEqual(fh.read(), expected)

    def test_vertical_capture_reports_a_64x128_picture(self):
        ser = script(device_info_message(), frame_message(0, orientation=f.ORIENTATION_VERTICAL))
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                f.capture_once(f.CaptureRequest("x", d + os.sep), connect=connector(ser))
        self.assertIn("native 64x128 PNG", out.getvalue())


class BurstTests(unittest.TestCase):
    def run_burst(self, ser, count, output, pause_ms=100):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = f.capture_burst(
                f.CaptureRequest("x", output), f.Burst(count, pause_ms), connect=connector(ser)
            )
        return rc, out.getvalue()

    def test_numbered_files_and_progress(self):
        with tempfile.TemporaryDirectory() as d:
            ser = script(*GOOD, frame_message(1), frame_message(2), frame_message(3))
            rc, out = self.run_burst(ser, 3, os.path.join(d, "shot.png"))
            self.assertEqual(rc, 0)
            self.assertEqual(
                sorted(os.listdir(d)), ["shot-1.png", "shot-2.png", "shot-3.png"]
            )
            self.assertIn("(3/3)", out)
            self.assertTrue(ser.closed)

    def test_folder_output_gets_timestamped_names(self):
        with tempfile.TemporaryDirectory() as d:
            ser = script(*GOOD, frame_message(1))
            self.run_burst(ser, 2, os.path.join(d, "new") + os.sep)
            names = os.listdir(os.path.join(d, "new"))
            self.assertEqual(len(names), 2)
            self.assertTrue(all(n.startswith("flipshot-Fliptest-") for n in names))

    @unittest.skipIf(sys.platform == "win32", "os.kill(SIGTERM) terminates the process on Windows")
    def test_sigterm_stops_an_endless_burst_cleanly(self):
        before = signal.getsignal(signal.SIGTERM)
        with tempfile.TemporaryDirectory() as d:
            ser = script(*GOOD)
            timer = threading.Timer(0.4, os.kill, (os.getpid(), signal.SIGTERM))
            timer.start()
            try:
                rc, out = self.run_burst(ser, -1, d + os.sep)
            finally:
                timer.cancel()
            self.assertEqual(rc, 0)
            self.assertIn("Stopped after", out)
            self.assertGreaterEqual(len(os.listdir(d)), 1)
        self.assertTrue(ser.closed)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)


if __name__ == "__main__":
    unittest.main()
