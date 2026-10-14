import contextlib
import io
import shlex
import unittest

from helpers import f


def parse(command):
    return f.parse_args(shlex.split(command))


class BurstArgsTests(unittest.TestCase):
    def test_absent_and_defaults(self):
        self.assertIsNone(parse("").burst)
        self.assertEqual(parse("-b").burst, f.Burst(10, 1000))

    def test_forms(self):
        cases = {
            "-b 5": (5, 1000),
            "-b 5 250": (5, 250),
            "-b -1 100": (-1, 100),
            "-b -1": (-1, 1000),
            "-b5": (5, 1000),
            "-b5 200": (5, 200),
            "--burst 3 300": (3, 300),
            "--burst=3": (3, 1000),
        }
        for command, expected in cases.items():
            self.assertEqual(tuple(parse(command).burst), expected, command)

    def test_port_after_burst_stays_positional(self):
        args = parse("-b 2 500 /dev/x -o a.png -s 3 -p")
        self.assertEqual(
            (args.burst, args.port, args.output, args.scale, args.paint),
            (f.Burst(2, 500), "/dev/x", "a.png", 3, True),
        )

    def test_malformed_forms_are_errors_not_ignored(self):
        for command in ("-bp", "-pb", "--bur 5", "-b 4 -b 5", "-b 1 2 3",
                        "-b 0", "-b 2 50", "-b 2 9999"):
            with self.subTest(command), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parse(command)


class ScaleArgsTests(unittest.TestCase):
    def test_plain_and_x_forms(self):
        self.assertEqual(parse("-s 3").scale, 3)
        self.assertEqual(parse("-s x3").scale, 3)
        self.assertEqual(parse("").scale, 1)

    def test_out_of_range(self):
        for command in ("-s 0", "-s 11", "-s abc"):
            with self.subTest(command), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parse(command)


if __name__ == "__main__":
    unittest.main()
