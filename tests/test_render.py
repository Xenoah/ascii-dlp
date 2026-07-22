import unittest

from ascii_dlp.errors import AsciiDlpError
from ascii_dlp.render import build_lut, fit_dimensions, frame_to_lines, validate_chars
from ascii_dlp.terminal import clip_cells, display_width


class RenderTests(unittest.TestCase):
    def test_lut_maps_dark_to_light(self) -> None:
        lut = build_lut(" .@", contrast=1.0)
        self.assertEqual(bytes([0, 255]).translate(lut), b" @")

    def test_inverted_lut(self) -> None:
        lut = build_lut(" .@", invert=True, contrast=1.0)
        self.assertEqual(bytes([0, 255]).translate(lut), b"@ ")

    def test_frame_to_lines(self) -> None:
        lut = build_lut(" @", contrast=1.0)
        lines = frame_to_lines(bytes([0, 255, 255, 0]), 2, 2, lut)
        self.assertEqual(lines, [" @", "@ "])

    def test_frame_size_is_checked(self) -> None:
        with self.assertRaises(ValueError):
            frame_to_lines(b"x", 2, 2, bytes(range(256)))

    def test_fit_widescreen_into_terminal(self) -> None:
        width, height = fit_dimensions(1920, 1080, 80, 40)
        self.assertEqual(width, 80)
        self.assertEqual(height, 22)

    def test_fit_respects_maximum(self) -> None:
        width, height = fit_dimensions(1920, 1080, 100, 50, max_width=40, max_height=10)
        self.assertLessEqual(width, 40)
        self.assertLessEqual(height, 10)

    def test_chars_must_be_ascii(self) -> None:
        with self.assertRaises(AsciiDlpError):
            validate_chars(" 黒")
        with self.assertRaises(AsciiDlpError):
            validate_chars(" \x1b@")

    def test_gamma_must_be_finite(self) -> None:
        with self.assertRaises(AsciiDlpError):
            build_lut(" @", gamma=float("nan"))

    def test_terminal_cell_width(self) -> None:
        self.assertEqual(display_width("A動画"), 5)
        self.assertEqual(clip_cells("A動画B", 5), "A動画")


if __name__ == "__main__":
    unittest.main()
