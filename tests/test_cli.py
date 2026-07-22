import argparse
import unittest

from ascii_dlp.cli import _finite_float, _positive_float, parse_timestamp


class ParseTimestampTests(unittest.TestCase):
    def test_seconds(self) -> None:
        self.assertEqual(parse_timestamp("12.5"), 12.5)

    def test_minutes_and_seconds(self) -> None:
        self.assertEqual(parse_timestamp("01:30"), 90.0)

    def test_hours_minutes_and_seconds(self) -> None:
        self.assertEqual(parse_timestamp("1:02:03.5"), 3723.5)

    def test_invalid_timestamp(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_timestamp("1::2")

    def test_negative_timestamp(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_timestamp("-1")

    def test_non_finite_numbers_are_rejected(self) -> None:
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    parse_timestamp(value)
                with self.assertRaises(argparse.ArgumentTypeError):
                    _positive_float(value)
                with self.assertRaises(argparse.ArgumentTypeError):
                    _finite_float(value)


if __name__ == "__main__":
    unittest.main()
