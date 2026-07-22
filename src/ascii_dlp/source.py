from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import Any

from .errors import AsciiDlpError
from .toolchain import Toolchain

ProgressCallback = Callable[[str], None]
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _noop_progress(_message: str) -> None:
    return None


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    fps: float
    duration: float | None
    has_audio: bool
    is_live: bool = False


@dataclass
class ResolvedSource:
    location: str
    title: str
    media: MediaInfo
    input_options: tuple[str, ...] = ()
    is_stream: bool = False
    _temporary: tempfile.TemporaryDirectory[str] | None = field(default=None, repr=False)

    def cleanup(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None


def parse_rate(value: str | int | float | None) -> float:
    if value in (None, "", "0/0"):
        return 0.0
    try:
        if isinstance(value, str) and "/" in value:
            result = float(Fraction(value))
        else:
            result = float(value)
        return result if math.isfinite(result) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def parse_aspect(value: str | None) -> float:
    if not value:
        return 0.0
    return parse_rate(value.replace(":", "/"))


def _display_dimensions(video: dict[str, Any]) -> tuple[int, int]:
    coded_width = int(video.get("width") or 0)
    coded_height = int(video.get("height") or 0)
    if coded_width <= 0 or coded_height <= 0:
        return coded_width, coded_height

    display_aspect = parse_aspect(video.get("display_aspect_ratio"))
    if not display_aspect:
        sample_aspect = parse_aspect(video.get("sample_aspect_ratio")) or 1.0
        display_aspect = coded_width * sample_aspect / coded_height
    display_width = max(1, round(coded_height * display_aspect))
    display_height = coded_height

    rotation: Any = (video.get("tags") or {}).get("rotate")
    for side_data in video.get("side_data_list") or []:
        if side_data.get("rotation") is not None:
            rotation = side_data["rotation"]
            break
    try:
        quarter_turn = round(float(rotation) / 90) % 2
    except (TypeError, ValueError):
        quarter_turn = 0
    if quarter_turn:
        display_width, display_height = display_height, display_width
    return display_width, display_height


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def probe_media(
    location: str,
    toolchain: Toolchain,
    input_options: Iterable[str] = (),
) -> MediaInfo:
    command = [
        toolchain.ffprobe,
        "-v",
        "error",
        *input_options,
        "-show_entries",
        (
            "format=duration:"
            "stream=codec_type,width,height,display_aspect_ratio,sample_aspect_ratio,"
            "avg_frame_rate,r_frame_rate,duration:"
            "stream_tags=rotate:stream_side_data=rotation"
        ),
        "-of",
        "json",
        location,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AsciiDlpError(f"動画情報を取得できませんでした: {exc}") from exc

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        suffix = f" ({detail[-1]})" if detail else ""
        raise AsciiDlpError(f"FFmpegが動画を開けませんでした{suffix}")

    try:
        payload = json.loads(result.stdout)
        streams = payload.get("streams") or []
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
    except (json.JSONDecodeError, StopIteration, TypeError) as exc:
        raise AsciiDlpError("映像トラックが見つかりません。") from exc

    width, height = _display_dimensions(video)
    if width <= 0 or height <= 0:
        raise AsciiDlpError("動画の解像度を取得できませんでした。")

    fps = parse_rate(video.get("avg_frame_rate")) or parse_rate(video.get("r_frame_rate"))
    duration = _positive_float((payload.get("format") or {}).get("duration"))
    duration = duration or _positive_float(video.get("duration"))
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    return MediaInfo(width, height, fps or 30.0, duration, has_audio)


def _load_ytdlp() -> tuple[Any, type[Exception]]:
    try:
        import yt_dlp
        from yt_dlp.utils import DownloadError
    except ImportError as exc:
        raise AsciiDlpError(
            "yt-dlp が未インストールです。"
            "`python -m pip install -e .` を実行してください。"
        ) from exc
    return yt_dlp, DownloadError


def _cookies_tuple(spec: str | None) -> tuple[str, str | None, None, None] | None:
    if not spec:
        return None
    browser, separator, profile = spec.partition(":")
    return browser, profile if separator and profile else None, None, None


def _safe_headers(info: dict[str, Any]) -> tuple[str, ...]:
    headers = info.get("http_headers") or {}
    lines: list[str] = []
    for raw_name, raw_value in headers.items():
        name = str(raw_name).replace("\r", "").replace("\n", "").strip()
        value = str(raw_value).replace("\r", "").replace("\n", "").strip()
        if name and value:
            lines.append(f"{name}: {value}\r\n")
    return ("-headers", "".join(lines)) if lines else ()


def _first_video(info: dict[str, Any]) -> dict[str, Any]:
    entries = info.get("entries")
    if entries is not None:
        try:
            entry = next(item for item in entries if item)
        except StopIteration as exc:
            raise AsciiDlpError(
                "プレイリスト内に再生可能な動画がありません。"
            ) from exc
        if not isinstance(entry, dict):
            raise AsciiDlpError("動画情報の形式を解釈できませんでした。")
        return entry
    return info


class _YtdlpLogger:
    def __init__(self, progress: ProgressCallback) -> None:
        self._progress = progress

    def debug(self, message: str) -> None:
        return None

    def info(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        self._progress(f"警告: {_ANSI_RE.sub('', message)}")

    def error(self, message: str) -> None:
        return None


def _base_ytdlp_options(
    *,
    progress: ProgressCallback,
    cookies_from_browser: str | None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "logger": _YtdlpLogger(progress),
        "noplaylist": True,
        "playlist_items": "1",
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
    }
    cookies = _cookies_tuple(cookies_from_browser)
    if cookies:
        options["cookiesfrombrowser"] = cookies
    return options


def _progress_hook(progress: ProgressCallback) -> Callable[[dict[str, Any]], None]:
    last_percent = {"value": ""}

    def hook(status: dict[str, Any]) -> None:
        state = status.get("status")
        if state == "downloading":
            percent = _ANSI_RE.sub("", str(status.get("_percent_str") or "")).strip()
            speed = _ANSI_RE.sub("", str(status.get("_speed_str") or "")).strip()
            eta = _ANSI_RE.sub("", str(status.get("_eta_str") or "")).strip()
            if percent and percent != last_percent["value"]:
                last_percent["value"] = percent
                parts = (percent, speed, f"ETA {eta}" if eta else "")
                details = " ".join(part for part in parts if part)
                progress(f"ダウンロード中: {details}")
        elif state == "finished":
            progress("ダウンロード完了。映像と音声を準備中…")

    return hook


def _candidate_files(
    directory: Path,
    before: set[Path],
    observed: list[str],
) -> list[Path]:
    ignored_suffixes = {
        ".ass",
        ".description",
        ".info.json",
        ".jpg",
        ".json",
        ".part",
        ".srt",
        ".vtt",
        ".webp",
        ".ytdl",
    }
    candidates: list[Path] = []
    for raw in observed:
        if raw:
            candidates.append(Path(raw))
    fallback = [
        path for path in directory.iterdir() if path.is_file() and path not in before
    ]
    candidates.extend(sorted(fallback, key=lambda item: item.stat().st_mtime, reverse=True))
    unique: dict[Path, None] = {}
    for path in candidates:
        ignored = any(str(path).endswith(suffix) for suffix in ignored_suffixes)
        if path.is_file() and not ignored:
            unique[path] = None
    return list(unique)


def _download(
    source: str,
    *,
    toolchain: Toolchain,
    quality: int,
    format_selector: str | None,
    cookies_from_browser: str | None,
    download_directory: Path | None,
    progress: ProgressCallback,
) -> ResolvedSource:
    yt_dlp, download_error = _load_ytdlp()
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        if download_directory is None:
            temporary = tempfile.TemporaryDirectory(prefix="ascii-dlp-")
            directory = Path(temporary.name)
        else:
            directory = download_directory.expanduser().resolve()
            directory.mkdir(parents=True, exist_ok=True)
        before = set(directory.iterdir())
    except OSError as exc:
        if temporary:
            temporary.cleanup()
        raise AsciiDlpError(
            f"ダウンロード先を準備できませんでした: {exc}"
        ) from exc
    observed: list[str] = []

    def postprocessor_hook(status: dict[str, Any]) -> None:
        info = status.get("info_dict") or {}
        for key in ("filepath", "_filename"):
            if info.get(key):
                observed.append(str(info[key]))

    options = _base_ytdlp_options(
        progress=progress,
        cookies_from_browser=cookies_from_browser,
    )
    options.update(
        {
            "format": format_selector
            or f"bv*[height<={quality}]+ba/b[height<={quality}]/bv*+ba/b",
            "ffmpeg_location": toolchain.ffmpeg_directory,
            "outtmpl": str(directory / "%(title).150B [%(id)s].%(ext)s"),
            "progress_hooks": [_progress_hook(progress)],
            "postprocessor_hooks": [postprocessor_hook],
        }
    )

    progress("動画を取得中…")
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            result = downloader.extract_info(source, download=True)
            info = _first_video(result)
            preferred: list[str] = []
            for requested in info.get("requested_downloads") or []:
                if requested.get("filepath"):
                    preferred.append(str(requested["filepath"]))
            for key in ("filepath", "_filename"):
                if info.get(key):
                    preferred.append(str(info[key]))
            preferred.extend(reversed(observed))
    except download_error as exc:
        if temporary:
            temporary.cleanup()
        raise AsciiDlpError(f"yt-dlpで動画を取得できませんでした: {exc}") from exc
    except Exception:
        if temporary:
            temporary.cleanup()
        raise

    try:
        candidates = _candidate_files(directory, before, preferred)
    except OSError as exc:
        if temporary:
            temporary.cleanup()
        raise AsciiDlpError(
            f"ダウンロード先のファイルを確認できませんでした: {exc}"
        ) from exc

    for candidate in candidates:
        try:
            media = probe_media(str(candidate), toolchain)
        except AsciiDlpError:
            continue
        title = str(info.get("title") or candidate.stem)
        return ResolvedSource(str(candidate), title, media, _temporary=temporary)

    if temporary:
        temporary.cleanup()
    raise AsciiDlpError(
        "ダウンロード後の動画ファイルを特定できませんでした。"
    )


def _stream_info(
    source: str,
    *,
    quality: int,
    format_selector: str | None,
    cookies_from_browser: str | None,
    progress: ProgressCallback,
) -> dict[str, Any]:
    yt_dlp, download_error = _load_ytdlp()
    options = _base_ytdlp_options(
        progress=progress,
        cookies_from_browser=cookies_from_browser,
    )
    options.update(
        {
            "format": format_selector
            or (
                f"b[height<={quality}][vcodec!=none][acodec!=none]/"
                f"bv*[height<={quality}]+ba/"
                "b[vcodec!=none][acodec!=none]/bv*+ba"
            ),
            "skip_download": True,
        }
    )
    progress("URLを解析中…")
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            return _first_video(downloader.extract_info(source, download=False))
    except download_error as exc:
        raise AsciiDlpError(f"yt-dlpでURLを解析できませんでした: {exc}") from exc


def _media_from_ytdlp(info: dict[str, Any]) -> MediaInfo:
    formats = info.get("requested_formats") or [info]
    video = next(
        (
            item
            for item in formats
            if item.get("vcodec") != "none" and item.get("width") and item.get("height")
        ),
        info,
    )
    width = int(video.get("width") or info.get("width") or 0)
    height = int(video.get("height") or info.get("height") or 0)
    if width <= 0 or height <= 0:
        raise AsciiDlpError("動画の解像度を取得できませんでした。")

    display_aspect = _positive_float(video.get("aspect_ratio"))
    if display_aspect:
        width = max(1, round(height * display_aspect))
    rotation = video.get("rotation")
    try:
        if round(float(rotation) / 90) % 2:
            width, height = height, width
    except (TypeError, ValueError):
        pass

    fps = _positive_float(video.get("fps")) or _positive_float(info.get("fps")) or 30.0
    duration = _positive_float(info.get("duration"))
    has_audio = any(item.get("acodec") not in (None, "none") for item in formats)
    is_live = bool(info.get("is_live")) or info.get("live_status") == "is_live"
    return MediaInfo(width, height, fps, duration, has_audio, is_live)


def inspect_source(
    source: str,
    *,
    toolchain: Toolchain | None,
    quality: int = 720,
    format_selector: str | None = None,
    cookies_from_browser: str | None = None,
    progress: ProgressCallback = _noop_progress,
) -> ResolvedSource:
    local = Path(source).expanduser()
    if local.is_file():
        if toolchain is None:
            raise AsciiDlpError("ローカル動画の情報取得にはffprobeが必要です。")
        resolved = local.resolve()
        return ResolvedSource(str(resolved), resolved.name, probe_media(str(resolved), toolchain))

    info = _stream_info(
        source,
        quality=quality,
        format_selector=format_selector,
        cookies_from_browser=cookies_from_browser,
        progress=progress,
    )
    return ResolvedSource(
        str(info.get("url") or ""),
        str(info.get("title") or "video"),
        _media_from_ytdlp(info),
        is_stream=True,
    )


def resolve_source(
    source: str,
    *,
    toolchain: Toolchain,
    quality: int = 720,
    format_selector: str | None = None,
    cookies_from_browser: str | None = None,
    download_first: bool = False,
    download_directory: Path | None = None,
    progress: ProgressCallback = _noop_progress,
) -> ResolvedSource:
    local = Path(source).expanduser()
    if local.is_file():
        resolved = local.resolve()
        return ResolvedSource(str(resolved), resolved.name, probe_media(str(resolved), toolchain))

    if download_first or download_directory is not None or cookies_from_browser:
        return _download(
            source,
            toolchain=toolchain,
            quality=quality,
            format_selector=format_selector,
            cookies_from_browser=cookies_from_browser,
            download_directory=download_directory,
            progress=progress,
        )

    info = _stream_info(
        source,
        quality=quality,
        format_selector=format_selector,
        cookies_from_browser=None,
        progress=progress,
    )
    direct_url = info.get("url")
    separate_formats = bool(info.get("requested_formats"))
    combined = info.get("vcodec") != "none" and info.get("acodec") != "none"
    if not direct_url or separate_formats or not combined:
        progress("単一ストリームがないため、一時ファイルへ取得します…")
        return _download(
            source,
            toolchain=toolchain,
            quality=quality,
            format_selector=format_selector,
            cookies_from_browser=None,
            download_directory=None,
            progress=progress,
        )

    input_options = _safe_headers(info)
    try:
        media = probe_media(str(direct_url), toolchain, input_options)
    except AsciiDlpError:
        progress("直接再生できないため、一時ファイルへ取得します…")
        return _download(
            source,
            toolchain=toolchain,
            quality=quality,
            format_selector=format_selector,
            cookies_from_browser=None,
            download_directory=None,
            progress=progress,
        )

    is_live = bool(info.get("is_live")) or info.get("live_status") == "is_live"
    media = replace(media, is_live=is_live)
    return ResolvedSource(
        str(direct_url),
        str(info.get("title") or "video"),
        media,
        input_options=input_options,
        is_stream=True,
    )
