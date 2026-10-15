import unittest
from types import SimpleNamespace

from helpers import f


def port(device="", description="", manufacturer="", vid=None, pid=None):
    return SimpleNamespace(
        device=device, description=description, manufacturer=manufacturer, vid=vid, pid=pid
    )


class IsFlipperPortTests(unittest.TestCase):
    def test_matches(self):
        for name, info in {
            "macOS device path": port(device="/dev/cu.usbmodemflip_Name1"),
            "description": port(description="Flipper Zero"),
            "manufacturer": port(manufacturer="Flipper Devices Inc."),
            "stock ST id": port(vid=0x0483, pid=0x5740),
        }.items():
            with self.subTest(name):
                self.assertTrue(f.is_flipper_port(info))

    def test_non_matches(self):
        for name, info in {
            "unrelated port": port(device="/dev/cu.usbserial-0001", description="USB Serial"),
            "other ST product id": port(vid=0x0483, pid=0x374B),
            "empty fields": port(device=None, description=None, manufacturer=None),
        }.items():
            with self.subTest(name):
                self.assertFalse(f.is_flipper_port(info))


if __name__ == "__main__":
    unittest.main()
