import struct
import unittest
import zlib

from helpers import f

BLANK = f.Frame(bytes(f.FRAME_BYTES))


def decode_png(png):
    """Minimal decoder for the PNGs we write: 1-bit, filter 0, one IDAT.
    Returns (width, height, color_type, palette_or_None, rows of 0/1 samples)."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    chunks, i = [], 8
    while i < len(png):
        length, kind = struct.unpack(">I4s", png[i:i + 8])
        data = png[i + 8:i + 8 + length]
        (crc,) = struct.unpack(">I", png[i + 8 + length:i + 12 + length])
        assert crc == zlib.crc32(kind + data) & 0xFFFFFFFF, kind
        chunks.append((kind, data))
        i += 12 + length
    kinds = [k for k, _ in chunks]
    assert kinds[0] == b"IHDR" and kinds[-1] == b"IEND"
    width, height, depth, color_type, *_ = struct.unpack(">IIBBBBB", chunks[0][1])
    assert depth == 1
    palette = dict(chunks).get(b"PLTE")
    raw = zlib.decompress(b"".join(d for k, d in chunks if k == b"IDAT"))
    stride = (width + 7) // 8 + 1
    rows = []
    for y in range(height):
        line = raw[y * stride:(y + 1) * stride]
        assert line[0] == 0
        rows.append([(line[1 + x // 8] >> (7 - x % 8)) & 1 for x in range(width)])
    return width, height, color_type, palette, rows


def frame_with(*pixels, orientation=f.ORIENTATION_HORIZONTAL):
    """A frame whose display buffer has the given (x, y) pixels set to ink."""
    data = bytearray(f.FRAME_BYTES)
    for x, y in pixels:
        data[(y // 8) * f.SCREEN_W + x] |= 1 << (y % 8)
    return f.Frame(bytes(data), orientation)


def ink_pixels(png):
    rows = decode_png(png)[4]
    return {(x, y) for y, row in enumerate(rows) for x, v in enumerate(row) if v == 0}


class RenderPngTests(unittest.TestCase):
    def test_blank_frame_is_all_background(self):
        w, h, color_type, palette, rows = decode_png(f.render_png(BLANK))
        self.assertEqual((w, h, color_type, palette), (128, 64, 0, None))
        self.assertTrue(all(sample == 1 for row in rows for sample in row))

    def test_ink_lands_on_the_right_pixel(self):
        points = [(0, 0), (127, 0), (0, 63), (127, 63), (5, 7), (5, 8), (64, 33)]
        rows = decode_png(f.render_png(frame_with(*points)))[4]
        ink = {(x, y) for y, row in enumerate(rows) for x, v in enumerate(row) if v == 0}
        self.assertEqual(ink, set(points))

    def test_full_frame_is_all_ink(self):
        rows = decode_png(f.render_png(f.Frame(b"\xff" * f.FRAME_BYTES)))[4]
        self.assertTrue(all(sample == 0 for row in rows for sample in row))

    def test_scale_makes_blocks(self):
        for scale in (2, 3, 10):
            with self.subTest(scale=scale):
                w, h, _c, _p, rows = decode_png(f.render_png(frame_with((3, 2)), scale))
                self.assertEqual((w, h), (128 * scale, 64 * scale))
                ink = {(x, y) for y, row in enumerate(rows) for x, v in enumerate(row) if v == 0}
                self.assertEqual(
                    ink,
                    {(3 * scale + dx, 2 * scale + dy) for dx in range(scale) for dy in range(scale)},
                )

    def test_paint_uses_two_color_palette(self):
        _w, _h, color_type, palette, rows = decode_png(f.render_png(frame_with((1, 1)), paint=True))
        self.assertEqual(color_type, 3)
        self.assertEqual(palette, bytes(f.PAINT_INK + f.PAINT_BACKGROUND))
        self.assertEqual((rows[1][1], rows[0][0]), (0, 1))

    def test_save_frame_writes_the_png(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.png")
            f.save_frame(frame_with((9, 9)), path, scale=2)
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), f.render_png(frame_with((9, 9)), 2))


# Buffer pixel (bx, by) must land here in the upright picture. These are written as
# plain geometry (turn the 128x64 buffer, then read it), not as the code's formulas.
#   180:          (x, y) -> (127 - x, 63 - y)
#   90 clockwise: (x, y) -> (63 - y, x)        (the picture is 64 wide, 128 tall)
#   90 counter:   (x, y) -> (y, 127 - x)
UPRIGHT = {
    f.ORIENTATION_HORIZONTAL: lambda x, y: (x, y),
    f.ORIENTATION_HORIZONTAL_FLIP: lambda x, y: (127 - x, 63 - y),
    f.ORIENTATION_VERTICAL: lambda x, y: (63 - y, x),
    f.ORIENTATION_VERTICAL_FLIP: lambda x, y: (y, 127 - x),
}
SAMPLE = [(0, 0), (127, 0), (0, 63), (127, 63), (10, 3), (100, 40)]


class OrientationTests(unittest.TestCase):
    def test_every_orientation_lands_pixels_upright(self):
        for orientation, upright in UPRIGHT.items():
            with self.subTest(orientation=orientation):
                png = f.render_png(frame_with(*SAMPLE, orientation=orientation))
                self.assertEqual(ink_pixels(png), {upright(x, y) for x, y in SAMPLE})

    def test_left_handed_frame_is_the_buffer_turned_around(self):
        # The top-left of the display buffer is the bottom-right of what you see.
        png = f.render_png(frame_with((0, 0), orientation=f.ORIENTATION_HORIZONTAL_FLIP))
        self.assertEqual(ink_pixels(png), {(127, 63)})

    def test_vertical_pictures_are_64_by_128(self):
        for orientation in (f.ORIENTATION_VERTICAL, f.ORIENTATION_VERTICAL_FLIP):
            for scale in (1, 3):
                with self.subTest(orientation=orientation, scale=scale):
                    w, h, *_ = decode_png(f.render_png(frame_with(orientation=orientation), scale))
                    self.assertEqual((w, h), (64 * scale, 128 * scale))

    def test_horizontal_pictures_stay_128_by_64(self):
        w, h, *_ = decode_png(f.render_png(frame_with(orientation=f.ORIENTATION_HORIZONTAL_FLIP)))
        self.assertEqual((w, h), (128, 64))

    def test_unknown_orientation_is_treated_as_horizontal(self):
        png = f.render_png(frame_with((5, 6), orientation=99))
        self.assertEqual(ink_pixels(png), {(5, 6)})

    def test_scale_and_paint_work_when_rotated(self):
        frame = frame_with((0, 0), orientation=f.ORIENTATION_VERTICAL)
        w, h, color_type, palette, rows = decode_png(f.render_png(frame, scale=2, paint=True))
        self.assertEqual((w, h, color_type), (128, 256, 3))
        # buffer (0, 0) -> upright (63, 0) -> a 2x2 block at x 126-127, y 0-1
        self.assertEqual(
            {(x, y) for y, row in enumerate(rows) for x, v in enumerate(row) if v == 0},
            {(126, 0), (127, 0), (126, 1), (127, 1)},
        )

    def test_saved_message_reports_the_real_size(self):
        vertical = f.Frame(bytes(f.FRAME_BYTES), f.ORIENTATION_VERTICAL)
        self.assertEqual(f.saved_message("a.png", 1, vertical), "Saved native 64x128 PNG to a.png")
        self.assertEqual(
            f.saved_message("a.png", 3, vertical), "Saved 192x384 PNG (x3) to a.png"
        )
        self.assertEqual(
            f.saved_message("a.png", 1, BLANK), "Saved native 128x64 PNG to a.png"
        )


if __name__ == "__main__":
    unittest.main()
