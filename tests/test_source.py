import unittest

from ascii_dlp.source import (
    _base_ytdlp_options,
    _cookies_tuple,
    _display_dimensions,
    _media_from_ytdlp,
    _safe_headers,
    parse_rate,
)


class ParseRateTests(unittest.TestCase):
    def test_fraction(self) -> None:
        self.assertAlmostEqual(parse_rate("30000/1001"), 29.97002997)

    def test_number(self) -> None:
        self.assertEqual(parse_rate(24), 24.0)

    def test_unknown(self) -> None:
        self.assertEqual(parse_rate("0/0"), 0.0)
        self.assertEqual(parse_rate(None), 0.0)
        self.assertEqual(parse_rate("inf"), 0.0)

    def test_browser_cookie_spec(self) -> None:
        self.assertEqual(
            _cookies_tuple("firefox:default-release"),
            ("firefox", "default-release", None, None),
        )

    def test_http_header_newlines_are_removed(self) -> None:
        options = _safe_headers({"http_headers": {"X-Test\r\nInjected": "safe\r\nvalue"}})
        self.assertEqual(options, ("-headers", "X-TestInjected: safevalue\r\n"))

    def test_display_aspect_and_rotation(self) -> None:
        video = {
            "width": 720,
            "height": 576,
            "display_aspect_ratio": "4:3",
            "side_data_list": [{"rotation": -90}],
        }
        self.assertEqual(_display_dimensions(video), (576, 768))

    def test_playlist_is_limited_to_first_item(self) -> None:
        options = _base_ytdlp_options(
            progress=lambda _message: None,
            cookies_from_browser=None,
        )
        self.assertEqual(options["playlist_items"], "1")

    def test_split_format_metadata_does_not_require_download(self) -> None:
        media = _media_from_ytdlp(
            {
                "duration": 10,
                "requested_formats": [
                    {"vcodec": "h264", "acodec": "none", "width": 1280, "height": 720},
                    {"vcodec": "none", "acodec": "aac"},
                ],
            }
        )
        self.assertEqual((media.width, media.height), (1280, 720))
        self.assertTrue(media.has_audio)

    def test_live_metadata_is_preserved(self) -> None:
        media = _media_from_ytdlp(
            {
                "width": 640,
                "height": 360,
                "vcodec": "h264",
                "acodec": "aac",
                "is_live": True,
            }
        )
        self.assertTrue(media.is_live)
        self.assertIsNone(media.duration)


if __name__ == "__main__":
    unittest.main()
