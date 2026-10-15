import os
import re
import tempfile
import unittest

from helpers import f

DEFAULT_NAME = re.compile(r"flipshot-Fliptest-\d{4}-\d\d-\d\d--\d\d-\d\d-\d\d-\d{3}\.png$")


class DestinationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def test_no_output_uses_default_name_in_cwd(self):
        path = f.Destination(None).path_for("Fliptest")
        self.assertRegex(path, DEFAULT_NAME)
        self.assertEqual(os.path.dirname(path), "")

    def test_device_name_is_sanitised(self):
        path = f.Destination(None).path_for("../Evil Name/\\x")
        self.assertNotIn("/", path)
        self.assertNotIn("\\", path)

    def test_single_file_is_used_as_is(self):
        self.assertEqual(f.Destination("shot.png").path_for("Fliptest"), "shot.png")
        self.assertEqual(f.Destination("a/b.jpg").path_for("Fliptest", 5), "a/b.jpg")

    def test_single_file_without_extension_gets_png(self):
        self.assertEqual(f.Destination("shot").path_for("Fliptest"), "shot.png")
        self.assertEqual(f.Destination("out.d/shot").path_for("Fliptest"), "out.d/shot.png")

    def test_burst_file_is_numbered(self):
        d = f.Destination("shot.png", numbered=True)
        self.assertEqual(d.path_for("Fliptest", 1), "shot-1.png")
        self.assertEqual(d.path_for("Fliptest", 2), "shot-2.png")

    def test_burst_numbering_adds_png_extension(self):
        self.assertEqual(f.Destination("shot", numbered=True).path_for("x", 3), "shot-3.png")

    def test_trailing_slash_folder_is_created(self):
        folder = os.path.join(self.tmp, "new", "deep") + os.sep
        path = f.Destination(folder).path_for("Fliptest")
        self.assertTrue(os.path.isdir(folder))
        self.assertEqual(os.path.dirname(path), folder.rstrip(os.sep))
        self.assertRegex(os.path.basename(path), DEFAULT_NAME)

    def test_existing_directory_without_slash_is_a_folder(self):
        for numbered in (False, True):
            path = f.Destination(self.tmp, numbered).path_for("Fliptest", 7)
            self.assertEqual(os.path.dirname(path), self.tmp)
            self.assertRegex(os.path.basename(path), DEFAULT_NAME)

    def test_folder_is_never_numbered(self):
        folder = self.tmp + os.sep
        self.assertNotIn("-7.", f.Destination(folder, numbered=True).path_for("x", 7))


if __name__ == "__main__":
    unittest.main()
