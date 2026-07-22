from __future__ import annotations

import importlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import AsciiDlpError


def _executable_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _version_line(executable: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (result.stdout or result.stderr).splitlines()
    return line[0].strip() if line else None


def _usable_file(path: Path) -> str | None:
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    return str(path)


def _bundled_paths() -> tuple[str, str]:
    try:
        package = importlib.import_module("ffmpeg_binaries")
    except (ImportError, OSError) as exc:
        raise AsciiDlpError(
            "内蔵FFmpegパッケージを読み込めませんでした。"
            "ascii-dlpを再インストールしてください。"
        ) from exc

    try:
        ffmpeg = getattr(package, "FFMPEG_PATH", None)
        ffprobe = getattr(package, "FFPROBE_PATH", None)
        if not ffmpeg or not ffprobe:
            package.init()
            ffmpeg = getattr(package, "FFMPEG_PATH", None)
            ffprobe = getattr(package, "FFPROBE_PATH", None)
    except Exception as exc:
        raise AsciiDlpError(
            f"内蔵FFmpegを準備できませんでした: {exc}"
        ) from exc

    ffmpeg_path = _usable_file(Path(ffmpeg)) if ffmpeg else None
    ffprobe_path = _usable_file(Path(ffprobe)) if ffprobe else None
    if not ffmpeg_path or not ffprobe_path:
        raise AsciiDlpError(
            "内蔵FFmpegの実行ファイルが見つかりません。"
            "ascii-dlpを再インストールしてください。"
        )
    return ffmpeg_path, ffprobe_path


@dataclass(frozen=True)
class Toolchain:
    ffmpeg: str
    ffprobe: str
    bundled: bool = False

    @classmethod
    def discover(cls, location: str | None = None, *, require_audio: bool = False) -> Toolchain:
        # Kept for source compatibility. Audio playback no longer depends on ffplay.
        del require_audio
        if location:
            supplied = Path(location).expanduser()
            base = supplied if supplied.is_dir() else supplied.parent
            ffmpeg = base / _executable_name("ffmpeg")
            ffprobe = base / _executable_name("ffprobe")

            if supplied.is_file() and supplied.stem.lower() == "ffmpeg":
                ffmpeg = supplied

            ffmpeg_path = _usable_file(ffmpeg)
            ffprobe_path = _usable_file(ffprobe)
            missing = [
                name
                for name, value in (("ffmpeg", ffmpeg_path), ("ffprobe", ffprobe_path))
                if not value
            ]
            if missing:
                raise AsciiDlpError(
                    f"指定場所に {', '.join(missing)} が見つかりません: {base}"
                )
            return cls(ffmpeg_path, ffprobe_path)

        ffmpeg_path = shutil.which("ffmpeg")
        ffprobe_path = shutil.which("ffprobe")
        if ffmpeg_path and ffprobe_path:
            return cls(ffmpeg_path, ffprobe_path)

        try:
            ffmpeg_path, ffprobe_path = _bundled_paths()
        except AsciiDlpError as exc:
            missing = [
                name
                for name, value in (("ffmpeg", ffmpeg_path), ("ffprobe", ffprobe_path))
                if not value
            ]
            raise AsciiDlpError(
                f"{', '.join(missing)} がPATHにも内蔵パッケージにも見つかりません。{exc}"
            ) from exc
        return cls(ffmpeg_path, ffprobe_path, bundled=True)

    @property
    def ffmpeg_directory(self) -> str:
        return str(Path(self.ffmpeg).parent)

    def versions(self) -> dict[str, str | None]:
        return {
            "ffmpeg": _version_line(self.ffmpeg),
            "ffprobe": _version_line(self.ffprobe),
        }
