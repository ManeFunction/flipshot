import unittest

from helpers import FakeSerial, device_info_message, f, frame_message


class VarintTests(unittest.TestCase):
    def test_round_trip(self):
        for value in (0, 1, 127, 128, 300, 16384, 2**32, 2**63):
            decoded, end = f.decode_varint(f.encode_varint(value), 0)
            self.assertEqual(decoded, value)
            self.assertEqual(end, len(f.encode_varint(value)))

    def test_truncated(self):
        with self.assertRaises(ValueError):
            f.decode_varint(b"\x80", 0)

    def test_overlong(self):
        with self.assertRaises(ValueError):
            f.decode_varint(b"\xff" * 12, 0)


class FieldTests(unittest.TestCase):
    def test_iter_fields_all_wire_types(self):
        buf = (
            f.encode_varint_field(1, 300)
            + f.encode_tag(2, 1) + b"12345678"
            + f.encode_length_delimited(3, b"abc")
            + f.encode_tag(4, 5) + b"wxyz"
        )
        self.assertEqual(
            list(f.iter_fields(buf)),
            [(1, 0, 300), (2, 1, b"12345678"), (3, 2, b"abc"), (4, 5, b"wxyz")],
        )

    def test_truncated_inputs_raise_value_error(self):
        full = f.encode_length_delimited(3, b"abcdef")
        for cut in range(1, len(full)):
            with self.assertRaises(ValueError, msg=f"cut={cut}"):
                list(f.iter_fields(full[:cut]))

    def test_unknown_wire_type(self):
        with self.assertRaises(ValueError):
            list(f.iter_fields(f.encode_tag(1, 3)))


class RequestTests(unittest.TestCase):
    def test_requests_carry_command_id_and_empty_payload(self):
        cases = [
            (f.send_start_screen_stream, 1, f.FIELD_GUI_START_SCREEN_STREAM),
            (f.send_stop_screen_stream, 2, f.FIELD_GUI_STOP_SCREEN_STREAM),
            (f.send_device_info_request, 10, f.FIELD_SYSTEM_DEVICE_INFO_REQUEST),
        ]
        for send, command_id, field in cases:
            ser = FakeSerial()
            send(ser)
            body = ser.written[0][1:]  # one length byte
            self.assertEqual(ser.written[0][0], len(body))
            self.assertEqual(
                list(f.iter_fields(body)), [(1, 0, command_id), (field, 2, b"")]
            )


class DecodeEventTests(unittest.TestCase):
    def test_frame(self):
        msg = frame_message(0xAB)[2:]  # strip the 2-byte length prefix
        event = f.decode_event(msg)
        self.assertIsInstance(event, f.Frame)
        self.assertEqual(event.data, b"\xab" * f.FRAME_BYTES)
        self.assertEqual(event.orientation, f.ORIENTATION_HORIZONTAL)

    def test_frame_orientation(self):
        for orientation in (1, 2, 3):
            msg = frame_message(orientation=orientation)[2:]
            self.assertEqual(f.decode_event(msg).orientation, orientation)

    def test_device_info(self):
        msg = device_info_message("hardware.name", "Zed", has_next=True)[1:]
        self.assertEqual(f.decode_event(msg), f.DeviceInfoEvent("hardware.name", "Zed", True))
        last = device_info_message("x", "y")[1:]
        self.assertFalse(f.decode_event(last).has_next)

    def test_other_messages_are_ignored(self):
        self.assertIsNone(f.decode_event(f.encode_varint_field(1, 5)))

    def test_malformed_message_raises_value_error(self):
        with self.assertRaises(ValueError):
            f.decode_event(frame_message()[1:-10])


class ReadMessageTests(unittest.TestCase):
    def test_reads_one_message(self):
        raw = frame_message()
        ser = FakeSerial([("data", raw + b"tail")])
        self.assertEqual(len(f.read_message(ser)), len(raw) - 2)

    def test_idle_timeout_consumes_nothing(self):
        ser = FakeSerial([("delay", 5, frame_message())], timeout=0.001)
        with self.assertRaises(TimeoutError):
            f.read_message(ser)
        self.assertEqual(len(ser.events), 1)

    def test_late_tail_does_not_desync(self):
        raw = frame_message()
        ser = FakeSerial([("data", raw[:5]), ("delay", 0.5, raw[5:])], timeout=0.001)
        self.assertEqual(len(f.read_message(ser)), len(raw) - 2)
        self.assertEqual(ser.timeout, 0.001)

    def test_stalled_mid_message_is_an_error(self):
        raw = frame_message()
        ser = FakeSerial([("data", raw[:5]), ("gap",)])
        with self.assertRaises(RuntimeError):
            f.read_message(ser)

    def test_absurd_length_is_rejected(self):
        ser = FakeSerial([("data", f.encode_varint(f.MAX_MESSAGE_BYTES + 1))])
        with self.assertRaises(RuntimeError):
            f.read_message(ser)


class ReadFrameAndNameTests(unittest.TestCase):
    def test_either_order(self):
        for script in (
            [device_info_message(value="A"), frame_message(1)],
            [frame_message(1), device_info_message(value="A")],
        ):
            ser = FakeSerial([("data", m) for m in script])
            frame, name = f.read_frame_and_device_name(ser, 1.0)
            self.assertEqual(name, "A")
            self.assertEqual(frame.data, b"\x01" * f.FRAME_BYTES)

    def test_waits_for_last_device_info_entry(self):
        ser = FakeSerial([("data", m) for m in (
            device_info_message("hardware.model", "M", has_next=True),
            frame_message(),
            device_info_message("hardware.name", "B"),
        )])
        self.assertEqual(f.read_frame_and_device_name(ser, 1.0)[1], "B")

    def test_wait_for_latest_keeps_newest_valid(self):
        ser = FakeSerial([
            ("data", frame_message(2)),
            ("data", frame_message(9, size=10)),  # wrong size, ignored
            ("data", frame_message(3)),
        ], timeout=0.05)
        stream = f.ScreenStream(ser, "x", f.Frame(b"\x00" * f.FRAME_BYTES))
        self.assertEqual(stream.wait_for_latest(0.2).data, b"\x03" * f.FRAME_BYTES)
        self.assertEqual(stream.frame.data, b"\x03" * f.FRAME_BYTES)

    def test_wait_for_latest_follows_orientation_changes(self):
        ser = FakeSerial([("data", frame_message(5, orientation=f.ORIENTATION_HORIZONTAL_FLIP))],
                         timeout=0.05)
        stream = f.ScreenStream(ser, "x", f.Frame(b"\x00" * f.FRAME_BYTES))
        self.assertEqual(stream.wait_for_latest(0.2).orientation, f.ORIENTATION_HORIZONTAL_FLIP)


if __name__ == "__main__":
    unittest.main()
