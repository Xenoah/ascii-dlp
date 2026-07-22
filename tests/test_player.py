import unittest

from ascii_dlp.player import format_time


class FormatTimeTests(unittest.TestCase):
    def test_minutes(self) -> None:
        self.assertEqual(format_time(65.9), "01:05")

    def test_hours(self) -> None:
        self.assertEqual(format_time(3661), "1:01:01")

    def test_live(self) -> None:
        self.assertEqual(format_time(None), "LIVE")


if __name__ == "__main__":
    unittest.main()

