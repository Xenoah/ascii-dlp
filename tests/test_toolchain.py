import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ascii_dlp.errors import AsciiDlpError
from ascii_dlp.toolchain import Toolchain, _bundled_paths, _executable_name


def _make_executable(directory: str, name: str) -> Path:
    path = Path(directory, _executable_name(name))
    path.touch()
    path.chmod(0o755)
    return path


class ToolchainTests(unittest.TestCase):
    def test_explicit_directory_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ffmpeg = _make_executable(directory, "ffmpeg")
            ffprobe = _make_executable(directory, "ffprobe")

            tools = Toolchain.discover(directory)

            self.assertEqual(tools.ffmpeg, str(ffmpeg))
            self.assertEqual(tools.ffprobe, str(ffprobe))
            self.assertFalse(tools.bundled)

    def test_invalid_explicit_directory_does_not_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch("ascii_dlp.toolchain._bundled_paths") as bundled:
                with self.assertRaises(AsciiDlpError):
                    Toolchain.discover(directory)
            bundled.assert_not_called()

    def test_complete_path_toolchain_is_preferred(self) -> None:
        paths = {"ffmpeg": "/tools/ffmpeg", "ffprobe": "/tools/ffprobe"}
        with (
            mock.patch("ascii_dlp.toolchain.shutil.which", side_effect=paths.get),
            mock.patch("ascii_dlp.toolchain._bundled_paths") as bundled,
        ):
            tools = Toolchain.discover()

        self.assertEqual(tools.ffmpeg, paths["ffmpeg"])
        self.assertEqual(tools.ffprobe, paths["ffprobe"])
        self.assertFalse(tools.bundled)
        bundled.assert_not_called()

    def test_bundled_toolchain_is_the_fallback(self) -> None:
        with (
            mock.patch("ascii_dlp.toolchain.shutil.which", return_value=None),
            mock.patch(
                "ascii_dlp.toolchain._bundled_paths",
                return_value=("/package/ffmpeg", "/package/ffprobe"),
            ),
        ):
            tools = Toolchain.discover()

        self.assertEqual(tools.ffmpeg, "/package/ffmpeg")
        self.assertEqual(tools.ffprobe, "/package/ffprobe")
        self.assertTrue(tools.bundled)

    def test_bundled_package_can_initialize_missing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ffmpeg = _make_executable(directory, "ffmpeg")
            ffprobe = _make_executable(directory, "ffprobe")
            package = SimpleNamespace(FFMPEG_PATH=None, FFPROBE_PATH=None)

            def initialize() -> None:
                package.FFMPEG_PATH = str(ffmpeg)
                package.FFPROBE_PATH = str(ffprobe)

            package.init = initialize
            with mock.patch(
                "ascii_dlp.toolchain.importlib.import_module",
                return_value=package,
            ):
                self.assertEqual(_bundled_paths(), (str(ffmpeg), str(ffprobe)))


if __name__ == "__main__":
    unittest.main()
