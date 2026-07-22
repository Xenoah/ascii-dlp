from __future__ import annotations

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


@dataclass(frozen=True)
class Toolchain:
    ffmpeg: str
    ffprobe: str
    ffplay: str | None

    @classmethod
    def discover(cls, location: str | None = None, *, require_audio: bool = False) -> Toolchain:
        if location:
            supplied = Path(location).expanduser()
            base = supplied if supplied.is_dir() else supplied.parent
            ffmpeg = base / _executable_name("ffmpeg")
            ffprobe = base / _executable_name("ffprobe")
            ffplay = base / _executable_name("ffplay")

            if supplied.is_file() and supplied.stem.lower() == "ffmpeg":
                ffmpeg = supplied

            ffmpeg_path = _usable_file(ffmpeg)
            ffprobe_path = _usable_file(ffprobe)
            ffplay_path = _usable_file(ffplay)
        else:
            ffmpeg_path = shutil.which("ffmpeg")
            ffprobe_path = shutil.which("ffprobe")
            ffplay_path = shutil.which("ffplay")

        missing = [
            name
            for name, value in (("ffmpeg", ffmpeg_path), ("ffprobe", ffprobe_path))
            if not value
        ]
        if missing:
            raise AsciiDlpError(
                f"{', '.join(missing)} が見つかりません。"
                "FFmpegをインストールし、PATHを通してください。"
            )
        if require_audio and not ffplay_path:
            raise AsciiDlpError(
                "ffplay が見つかりません。"
                "音声なしなら --no-audio を指定できます。"
            )
        return cls(ffmpeg_path, ffprobe_path, ffplay_path)

    @property
    def ffmpeg_directory(self) -> str:
        return str(Path(self.ffmpeg).parent)

    def versions(self) -> dict[str, str | None]:
        return {
            "ffmpeg": _version_line(self.ffmpeg),
            "ffprobe": _version_line(self.ffprobe),
            "ffplay": _version_line(self.ffplay) if self.ffplay else None,
        }
