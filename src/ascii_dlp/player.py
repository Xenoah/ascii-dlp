from __future__ import annotations

import math
import os
import queue
import subprocess
import tempfile
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass
from enum import Enum
from typing import BinaryIO

from .errors import AsciiDlpError
from .render import DEFAULT_CHARS, build_lut, fit_dimensions, frame_to_lines
from .source import ResolvedSource
from .terminal import Key, KeyReader, TerminalScreen, wait_with_input
from .toolchain import Toolchain


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=0.7)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=0.7)
        except subprocess.TimeoutExpired:
            pass


def _read_exact(stream: BinaryIO, size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _pcm_stream(
    source: BinaryIO,
    *,
    channels: int = 2,
    sample_width: int = 2,
) -> Generator[bytes, int, None]:
    required_frames = yield b""
    bytes_per_frame = channels * sample_width
    while True:
        sample_data = source.read(required_frames * bytes_per_frame)
        if not sample_data:
            return
        required_frames = yield sample_data


def _error_from_file(stream: BinaryIO, redacted: str) -> str | None:
    stream.flush()
    stream.seek(0)
    text = stream.read().decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    detail = lines[-1].replace(redacted, "<source>")
    return " ".join(detail.split())[:300]


class FrameState(Enum):
    PENDING = "pending"
    EOF = "eof"


class VideoDecoder:
    def __init__(
        self,
        source: ResolvedSource,
        toolchain: Toolchain,
        *,
        position: float,
        width: int,
        height: int,
        fps: float,
    ) -> None:
        self.width = width
        self.height = height
        self._location = source.location
        self._stderr = tempfile.TemporaryFile()
        command = [
            toolchain.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{position:.6f}",
            *source.input_options,
            "-i",
            source.location,
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            f"setpts=PTS-STARTPTS,fps={fps:.6f},scale={width}:{height}:flags=area,format=gray",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "pipe:1",
        ]
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            self._stderr.close()
            raise AsciiDlpError(f"FFmpegを起動できませんでした: {exc}") from exc

        self._frames: queue.Queue[bytes | FrameState] = queue.Queue(maxsize=3)
        self._stop_event = threading.Event()
        self._reader = threading.Thread(
            target=self._read_frames,
            name="ascii-dlp-video-reader",
            daemon=True,
        )
        self._reader.start()

    def _queue_item(self, item: bytes | FrameState) -> None:
        while not self._stop_event.is_set():
            try:
                self._frames.put(item, timeout=0.05)
                return
            except queue.Full:
                continue

    def _read_frames(self) -> None:
        if self._process.stdout is None:
            self._queue_item(FrameState.EOF)
            return
        frame_size = self.width * self.height
        while not self._stop_event.is_set():
            frame = _read_exact(self._process.stdout, frame_size)
            if frame is None:
                self._queue_item(FrameState.EOF)
                return
            self._queue_item(frame)

    def read_frame(self, timeout: float = 0.05) -> bytes | FrameState:
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return FrameState.PENDING

    def error_detail(self) -> str | None:
        if self._process.poll() is None:
            try:
                self._process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                return None
        if self._process.returncode == 0:
            return None
        return _error_from_file(self._stderr, self._location)

    def stop(self) -> None:
        self._stop_event.set()
        _stop_process(self._process)
        self._reader.join(timeout=0.7)
        if self._process.stdout is not None:
            self._process.stdout.close()
        self._stderr.close()


class AudioPipeline:
    def __init__(
        self,
        source: ResolvedSource,
        toolchain: Toolchain,
        *,
        position: float,
        volume: int,
    ) -> None:
        self._decoder: subprocess.Popen[bytes] | None = None
        self._device = None
        self._stream: Generator[bytes, int, None] | None = None
        self._location = source.location
        self._decoder_stderr: BinaryIO | None = None

        try:
            import miniaudio
        except (ImportError, OSError) as exc:
            raise AsciiDlpError(
                "音声ライブラリを読み込めませんでした。"
                "ascii-dlpを再インストールしてください。"
            ) from exc

        self._decoder_stderr = tempfile.TemporaryFile()

        decoder_command = [
            toolchain.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{position:.6f}",
            *source.input_options,
            "-i",
            source.location,
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-af",
            f"volume={volume / 100:.4f}",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "s16le",
            "pipe:1",
        ]
        try:
            self._decoder = subprocess.Popen(
                decoder_command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=self._decoder_stderr,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            self._decoder_stderr.close()
            self._decoder_stderr = None
            raise AsciiDlpError(f"音声用FFmpegを起動できませんでした: {exc}") from exc

        assert self._decoder.stdout is not None
        try:
            self._device = miniaudio.PlaybackDevice(
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=2,
                sample_rate=48000,
                buffersize_msec=20,
                app_name="ascii-dlp",
            )
            self._stream = _pcm_stream(self._decoder.stdout)
            next(self._stream)
            self._device.start(self._stream)
        except Exception as exc:
            self.stop()
            raise AsciiDlpError(f"音声デバイスを開始できませんでした: {exc}") from exc

    def failure_detail(self) -> str | None:
        if self._decoder is None or self._decoder_stderr is None:
            return None
        returncode = self._decoder.poll()
        if returncode not in (None, 0):
            detail = _error_from_file(self._decoder_stderr, self._location)
            return f"audio FFmpeg: {detail or f'exit {returncode}'}"
        return None

    def stop(self) -> None:
        decoder = self._decoder
        self._decoder = None
        _stop_process(decoder)

        device = self._device
        self._device = None
        if device is not None:
            try:
                device.close()
            except Exception:
                pass

        stream = self._stream
        self._stream = None
        if stream is not None:
            stream.close()
        if decoder is not None and decoder.stdout is not None:
            decoder.stdout.close()

        decoder_stderr = self._decoder_stderr
        self._decoder_stderr = None
        if decoder_stderr is not None:
            decoder_stderr.close()


@dataclass(frozen=True)
class PlayerConfig:
    fps: float = 15.0
    max_width: int | None = None
    max_height: int | None = None
    chars: str = DEFAULT_CHARS
    invert: bool = False
    gamma: float = 1.0
    contrast: float = 1.1
    start: float = 0.0
    audio: bool = True
    volume: int = 80
    seek_step: float = 5.0
    long_seek_step: float = 30.0
    audio_latency_ms: float = 80.0


def format_time(seconds: float | None) -> str:
    if seconds is None:
        return "LIVE"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _safe_title(title: str) -> str:
    return "".join(char if ord(char) >= 32 and char != "\x7f" else " " for char in title)


def _status(
    source: ResolvedSource,
    position: float,
    dimensions: tuple[int, int],
    fps: float,
) -> str:
    duration = source.media.duration
    if duration is None:
        label = "LIVE" if source.media.is_live else "UNKNOWN"
        clock = f"{label} +{format_time(position)}"
    else:
        clock = f"{format_time(position)}/{format_time(duration)}"
    mode = "STREAM" if source.is_stream else "LOCAL"
    width, height = dimensions
    return f"{clock} | {width}x{height} {fps:g}fps | {mode} | {_safe_title(source.title)}"


def _seek_target(position: float, delta: float, duration: float | None) -> float:
    if duration is None:
        return position
    upper = max(0.0, duration - 0.05)
    return min(upper, max(0.0, position + delta))


def _action(events: list[Key], seek_step: float, long_seek_step: float) -> tuple[str, float] | None:
    for event in events:
        if event == Key.QUIT:
            return "quit", 0.0
        if event == Key.TOGGLE_PAUSE:
            return "pause", 0.0
        if event == Key.BACK:
            return "seek", -seek_step
        if event == Key.FORWARD:
            return "seek", seek_step
        if event == Key.BACK_LONG:
            return "seek", -long_seek_step
        if event == Key.FORWARD_LONG:
            return "seek", long_seek_step
    return None


def play(source: ResolvedSource, toolchain: Toolchain, config: PlayerConfig) -> None:
    if not math.isfinite(config.fps) or config.fps <= 0:
        raise AsciiDlpError("--fps は0より大きい値にしてください。")
    if not 0 <= config.volume <= 100:
        raise AsciiDlpError("--volume は0～100で指定してください。")
    if not math.isfinite(config.audio_latency_ms):
        raise AsciiDlpError("--audio-latency-ms は有限の数値にしてください。")
    if source.media.duration is not None and config.start >= source.media.duration:
        raise AsciiDlpError("--start が動画の長さを超えています。")

    lut = build_lut(
        config.chars,
        invert=config.invert,
        gamma=config.gamma,
        contrast=config.contrast,
    )
    fps = max(1.0, min(config.fps, source.media.fps or config.fps))
    position = max(0.0, config.start)
    last_lines = ["Loading…"]
    paused = False
    controls_footer = (
        f"Space:停止/再開  ←/→:±{config.seek_step:g}秒  "
        f"↑/↓:±{config.long_seek_step:g}秒  Q:終了"
    )
    audio_notice: str | None = None
    audio_disabled = False

    with TerminalScreen() as screen, KeyReader() as reader:
        while True:
            terminal_size = screen.size()
            if terminal_size.columns < 12 or terminal_size.rows <= TerminalScreen.HUD_ROWS + 1:
                raise AsciiDlpError(
                    "端末が小さすぎます。12列×4行以上に広げてください。"
                )
            dimensions = fit_dimensions(
                source.media.width,
                source.media.height,
                terminal_size.columns - 1,
                terminal_size.rows - TerminalScreen.HUD_ROWS,
                max_width=config.max_width,
                max_height=config.max_height,
            )

            if paused:
                screen.render(
                    last_lines,
                    _status(source, position, dimensions, fps),
                    paused=True,
                    footer=audio_notice or controls_footer,
                )
                while paused:
                    events = reader.poll()
                    action = _action(events, config.seek_step, config.long_seek_step)
                    if action:
                        kind, value = action
                        if kind == "quit":
                            return
                        if kind == "pause":
                            paused = False
                            break
                        if kind == "seek":
                            position = _seek_target(position, value, source.media.duration)
                            screen.render(
                                last_lines,
                                _status(source, position, dimensions, fps),
                                paused=True,
                                footer=audio_notice or controls_footer,
                            )
                    new_size = screen.size()
                    if new_size != terminal_size:
                        break
                    time.sleep(0.02)
                continue

            decoder = VideoDecoder(
                source,
                toolchain,
                position=position,
                width=dimensions[0],
                height=dimensions[1],
                fps=fps,
            )
            audio: AudioPipeline | None = None
            try:
                first_frame: bytes | FrameState = FrameState.PENDING
                while first_frame == FrameState.PENDING:
                    first_frame = decoder.read_frame()
                    action = _action(
                        reader.poll(),
                        config.seek_step,
                        config.long_seek_step,
                    )
                    if action and action[0] == "quit":
                        return
                if first_frame == FrameState.EOF:
                    detail = decoder.error_detail()
                    if detail:
                        raise AsciiDlpError(
                            f"FFmpegの映像デコードに失敗しました: {detail}"
                        )
                    if position <= 0.1:
                        raise AsciiDlpError(
                            "FFmpegから映像フレームを取得できませんでした。"
                        )
                    return
                assert isinstance(first_frame, bytes)
                last_lines = frame_to_lines(first_frame, *dimensions, lut)
                screen.render(
                    last_lines,
                    _status(source, position, dimensions, fps),
                    paused=False,
                    footer=audio_notice or controls_footer,
                )

                if config.audio and source.media.has_audio and not audio_disabled:
                    try:
                        audio = AudioPipeline(
                            source,
                            toolchain,
                            position=position,
                            volume=config.volume,
                        )
                    except AsciiDlpError as exc:
                        audio_disabled = True
                        audio_notice = f"Q:終了 | AUDIO OFF: {_safe_title(str(exc))}"
                        screen.render(
                            last_lines,
                            _status(source, position, dimensions, fps),
                            paused=False,
                            footer=audio_notice,
                        )
                anchor_position = position
                latency = config.audio_latency_ms / 1000.0 if audio else 0.0
                anchor_clock = time.monotonic() + latency
                frame_index = 0
                action: tuple[str, float] | None = None

                while True:
                    now = time.monotonic()
                    current = anchor_position + max(0.0, now - anchor_clock)
                    if source.media.duration is not None and current >= source.media.duration:
                        return

                    if audio:
                        audio_error = audio.failure_detail()
                        if audio_error:
                            audio.stop()
                            audio = None
                            audio_disabled = True
                            audio_notice = f"Q:終了 | AUDIO OFF: {_safe_title(audio_error)}"
                            screen.render(
                                last_lines,
                                _status(source, current, dimensions, fps),
                                paused=False,
                                footer=audio_notice,
                            )

                    action = _action(reader.poll(), config.seek_step, config.long_seek_step)
                    if action:
                        break
                    if screen.size() != terminal_size:
                        action = ("resize", 0.0)
                        break

                    frame = decoder.read_frame()
                    if frame == FrameState.PENDING:
                        continue
                    if frame == FrameState.EOF:
                        detail = decoder.error_detail()
                        if detail:
                            raise AsciiDlpError(
                                f"FFmpegの映像デコードに失敗しました: {detail}"
                            )
                        if (
                            source.is_stream
                            and source.media.duration is not None
                            and current < source.media.duration - 1.0
                        ):
                            raise AsciiDlpError(
                                "ストリームが途中で終了しました。"
                                "--download を付けて再実行してください。"
                            )
                        return
                    assert isinstance(frame, bytes)
                    frame_index += 1
                    frame_position = anchor_position + frame_index / fps

                    while True:
                        current = anchor_position + max(0.0, time.monotonic() - anchor_clock)
                        remaining = frame_position - current
                        if remaining <= 0:
                            break
                        events = wait_with_input(reader, min(remaining, 0.03))
                        action = _action(events, config.seek_step, config.long_seek_step)
                        if action:
                            break
                    if action:
                        break

                    current = anchor_position + max(0.0, time.monotonic() - anchor_clock)
                    if frame_position < current - (1.5 / fps):
                        continue
                    last_lines = frame_to_lines(frame, *dimensions, lut)
                    screen.render(
                        last_lines,
                        _status(source, min(current, frame_position), dimensions, fps),
                        paused=False,
                        footer=audio_notice or controls_footer,
                    )

                current = anchor_position + max(0.0, time.monotonic() - anchor_clock)
                kind, value = action or ("resize", 0.0)
                if kind == "quit":
                    return
                if kind == "pause":
                    position = current
                    paused = True
                elif kind == "seek":
                    position = _seek_target(current, value, source.media.duration)
                else:
                    position = current
            finally:
                if audio:
                    audio.stop()
                decoder.stop()
