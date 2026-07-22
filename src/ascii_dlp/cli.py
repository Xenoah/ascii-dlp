from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from . import __version__
from .errors import AsciiDlpError
from .player import PlayerConfig, format_time, play
from .render import DEFAULT_CHARS
from .source import inspect_source, resolve_source
from .toolchain import Toolchain


def parse_timestamp(value: str) -> float:
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError("時刻が空です")
    try:
        if ":" not in text:
            result = float(text)
        else:
            parts = text.split(":")
            if len(parts) > 3 or any(not part for part in parts):
                raise ValueError
            result = 0.0
            for part in parts:
                result = result * 60 + float(part)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "時刻は 秒、MM:SS、HH:MM:SS で指定してください"
        ) from exc
    if not math.isfinite(result) or result < 0:
        raise argparse.ArgumentTypeError("時刻は有限の0以上にしてください")
    return result


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("整数で指定してください") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError("0より大きい値にしてください")
    return result


def _finite_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("数値で指定してください") from exc
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError("有限の数値で指定してください")
    return result


def _positive_float(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("数値で指定してください") from exc
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("有限の0より大きい値にしてください")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ascii-dlp",
        description=(
            "yt-dlp + FFmpegで動画をターミナルのASCIIアートとして再生します。"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="動画URLまたはローカル動画ファイル。`doctor`も指定可",
    )
    parser.add_argument("--version", action="version", version=f"ascii-dlp {__version__}")
    parser.add_argument(
        "--doctor", action="store_true", help="依存ツールの診断だけ行う"
    )
    parser.add_argument(
        "--info", action="store_true", help="動画情報を表示して終了する"
    )

    source_group = parser.add_argument_group("動画の取得")
    source_group.add_argument(
        "--quality", type=_positive_int, default=720, help="取得する最大映像高"
    )
    source_group.add_argument(
        "--format", dest="format_selector", help="yt-dlpのフォーマット指定"
    )
    source_group.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER[:PROFILE]",
        help="ブラウザのCookieを利用する（この場合は先にダウンロード）",
    )
    source_group.add_argument(
        "--download",
        action="store_true",
        help="ストリーミングせず、一時ファイルへ取得してから再生する",
    )
    source_group.add_argument(
        "--download-dir",
        type=Path,
        help="取得した動画を削除せず、このフォルダーへ保存する",
    )
    source_group.add_argument(
        "--ffmpeg-location",
        help="外部ffmpeg/ffprobeがあるフォルダー、またはffmpeg実行ファイル",
    )

    display = parser.add_argument_group("表示")
    display.add_argument("--fps", type=_positive_float, default=15.0, help="ASCII描画FPS")
    display.add_argument("--max-width", type=_positive_int, help="ASCII映像の最大文字幅")
    display.add_argument("--max-height", type=_positive_int, help="ASCII映像の最大行数")
    display.add_argument("--chars", default=DEFAULT_CHARS, help="暗い順に並べたASCII文字")
    display.add_argument("--invert", action="store_true", help="明暗文字を反転する")
    display.add_argument("--gamma", type=_positive_float, default=1.0, help="ガンマ補正")
    display.add_argument(
        "--contrast", type=_positive_float, default=1.1, help="コントラスト補正"
    )

    playback = parser.add_argument_group("再生")
    playback.add_argument(
        "--start",
        type=parse_timestamp,
        default=0.0,
        help="再生開始位置（秒/MM:SS/HH:MM:SS）",
    )
    playback.add_argument(
        "--no-audio", "--mute", action="store_true", help="音声を再生しない"
    )
    playback.add_argument("--volume", type=int, default=80, help="音量（0～100）")
    playback.add_argument(
        "--seek-step", type=_positive_float, default=5.0, help="左右キーの移動秒数"
    )
    playback.add_argument(
        "--long-seek-step",
        type=_positive_float,
        default=30.0,
        help="上下キーの移動秒数",
    )
    playback.add_argument(
        "--audio-latency-ms",
        type=_finite_float,
        default=80.0,
        help="音声デバイス遅延の補正値（ミリ秒）",
    )
    return parser


class _Progress:
    def __init__(self) -> None:
        self._width = 0

    def __call__(self, message: str) -> None:
        clean = message.replace("\r", " ").replace("\n", " ")
        padding = " " * max(0, self._width - len(clean))
        sys.stderr.write(f"\r{clean}{padding}")
        sys.stderr.flush()
        self._width = len(clean)

    def finish(self) -> None:
        if self._width:
            sys.stderr.write("\r" + " " * self._width + "\r")
            sys.stderr.flush()
            self._width = 0


def _doctor(ffmpeg_location: str | None) -> int:
    print(f"ascii-dlp: {__version__}")
    print(f"Python: {sys.version.split()[0]}")
    okay = True
    try:
        import yt_dlp

        print(f"yt-dlp: {yt_dlp.version.__version__}")
    except ImportError:
        print("yt-dlp: NOT FOUND")
        okay = False

    try:
        import miniaudio

        version = getattr(miniaudio, "__version__", "unknown")
        library_version = miniaudio.lib_version()
        print(f"miniaudio: {version} (library {library_version})")
    except (ImportError, OSError, RuntimeError) as exc:
        print(f"miniaudio: NOT READY ({exc})")
        okay = False

    try:
        tools = Toolchain.discover(ffmpeg_location)
    except AsciiDlpError as exc:
        print(f"FFmpeg: NOT READY ({exc})")
        return 1
    for name, version in tools.versions().items():
        if version:
            display = version
        else:
            display = "NOT FOUND"
            okay = False
        print(f"{name}: {display}")
    print(f"FFmpeg source: {'bundled' if tools.bundled else 'external'}")
    print(f"stdin TTY: {'yes' if sys.stdin.isatty() else 'no'}")
    print(f"stdout TTY: {'yes' if sys.stdout.isatty() else 'no'}")
    return 0 if okay else 1


def _print_info(resolved: object) -> None:
    source = resolved
    payload = {
        "title": source.title,
        "resolution": f"{source.media.width}x{source.media.height}",
        "fps": round(source.media.fps, 3),
        "duration_seconds": source.media.duration,
        "duration": (
            format_time(source.media.duration)
            if source.media.duration is not None
            else "LIVE" if source.media.is_live else "UNKNOWN"
        ),
        "audio": source.media.has_audio,
        "live": source.media.is_live,
        "mode": "stream" if source.is_stream else "local",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.doctor or args.source == "doctor":
        return _doctor(args.ffmpeg_location)
    if not args.source:
        parser.error("動画URLまたはファイルを指定してください")
    if not 0 <= args.volume <= 100:
        parser.error("--volume は0～100で指定してください")

    remote_info = args.info and not Path(args.source).expanduser().is_file()
    toolchain = None if remote_info else Toolchain.discover(args.ffmpeg_location)

    progress = _Progress()
    resolved = None
    try:
        if args.info:
            resolved = inspect_source(
                args.source,
                toolchain=toolchain,
                quality=args.quality,
                format_selector=args.format_selector,
                cookies_from_browser=args.cookies_from_browser,
                progress=progress,
            )
        else:
            resolved = resolve_source(
                args.source,
                toolchain=toolchain,
                quality=args.quality,
                format_selector=args.format_selector,
                cookies_from_browser=args.cookies_from_browser,
                download_first=args.download,
                download_directory=args.download_dir,
                progress=progress,
            )
        progress.finish()
        if args.info:
            _print_info(resolved)
            return 0

        assert toolchain is not None
        config = PlayerConfig(
            fps=args.fps,
            max_width=args.max_width,
            max_height=args.max_height,
            chars=args.chars,
            invert=args.invert,
            gamma=args.gamma,
            contrast=args.contrast,
            start=args.start,
            audio=not args.no_audio,
            volume=args.volume,
            seek_step=args.seek_step,
            long_seek_step=args.long_seek_step,
            audio_latency_ms=args.audio_latency_ms,
        )
        play(resolved, toolchain, config)
        return 0
    finally:
        progress.finish()
        if resolved is not None:
            resolved.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args, parser)
    except KeyboardInterrupt:
        return 130
    except AsciiDlpError as exc:
        print(f"ascii-dlp: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
