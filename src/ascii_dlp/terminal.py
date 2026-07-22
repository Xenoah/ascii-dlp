from __future__ import annotations

import os
import select
import shutil
import sys
import time
import unicodedata
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import Enum
from types import TracebackType

from .errors import AsciiDlpError


class Key(str, Enum):
    QUIT = "quit"
    TOGGLE_PAUSE = "toggle_pause"
    BACK = "back"
    FORWARD = "forward"
    BACK_LONG = "back_long"
    FORWARD_LONG = "forward_long"


class KeyReader(AbstractContextManager["KeyReader"]):
    def __init__(self) -> None:
        self._fd: int | None = None
        self._saved: list[object] | None = None
        self._buffer = b""
        self._escape_started: float | None = None

    def __enter__(self) -> KeyReader:
        if not sys.stdin.isatty():
            raise AsciiDlpError("キー操作には対話端末が必要です。")
        if os.name != "nt":
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if os.name != "nt" and self._fd is not None and self._saved is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def poll(self) -> list[Key]:
        if os.name == "nt":
            return self._poll_windows()
        return self._poll_posix()

    def _poll_windows(self) -> list[Key]:
        import msvcrt

        events: list[Key] = []
        while msvcrt.kbhit():
            char = msvcrt.getwch()
            if char in ("\x00", "\xe0"):
                special = msvcrt.getwch()
                event = {
                    "K": Key.BACK,
                    "M": Key.FORWARD,
                    "H": Key.FORWARD_LONG,
                    "P": Key.BACK_LONG,
                }.get(special)
            else:
                event = _key_for_text(char)
            if event:
                events.append(event)
        return events

    def _poll_posix(self) -> list[Key]:
        assert self._fd is not None
        while select.select([self._fd], [], [], 0)[0]:
            chunk = os.read(self._fd, 32)
            if not chunk:
                break
            self._buffer += chunk

        return self._consume_buffer()

    def _consume_buffer(self) -> list[Key]:
        """Parse complete key sequences while retaining a partial CSI sequence."""

        events: list[Key] = []
        sequences = {
            b"\x1b[D": Key.BACK,
            b"\x1b[C": Key.FORWARD,
            b"\x1b[A": Key.FORWARD_LONG,
            b"\x1b[B": Key.BACK_LONG,
        }
        while self._buffer:
            matched = False
            for sequence, event in sequences.items():
                if self._buffer.startswith(sequence):
                    events.append(event)
                    self._buffer = self._buffer[len(sequence) :]
                    matched = True
                    break
            if matched:
                self._escape_started = None
                continue
            if self._buffer.startswith(b"\x1b"):
                if self._escape_started is None:
                    self._escape_started = time.monotonic()
                elapsed = time.monotonic() - self._escape_started
                if len(self._buffer) == 1:
                    if elapsed < 0.05:
                        break
                    self._buffer = self._buffer[1:]
                    self._escape_started = None
                    continue
                if self._buffer[1] in (ord("["), ord("O")):
                    final_index = next(
                        (
                            index
                            for index, value in enumerate(self._buffer[2:], start=2)
                            if 0x40 <= value <= 0x7E
                        ),
                        None,
                    )
                    if final_index is None and elapsed < 0.05:
                        break
                    if final_index is not None:
                        self._buffer = self._buffer[final_index + 1 :]
                        self._escape_started = None
                        continue
                self._buffer = self._buffer[1:]
                self._escape_started = None
                continue
            self._escape_started = None
            char = chr(self._buffer[0])
            self._buffer = self._buffer[1:]
            event = _key_for_text(char)
            if event:
                events.append(event)
        return events


def _key_for_text(char: str) -> Key | None:
    lowered = char.lower()
    if lowered == "q":
        return Key.QUIT
    if char == " ":
        return Key.TOGGLE_PAUSE
    if lowered in ("h", "a"):
        return Key.BACK
    if lowered in ("l", "d"):
        return Key.FORWARD
    if lowered in ("j", "s"):
        return Key.BACK_LONG
    if lowered in ("k", "w"):
        return Key.FORWARD_LONG
    return None


def _enable_windows_vt() -> tuple[int, int] | None:
    if os.name != "nt":
        return None
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)
    mode = ctypes.c_uint32()
    if handle == -1 or not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        raise AsciiDlpError(
            "このWindowsコンソールではANSI表示を有効化できません。"
        )
    original = int(mode.value)
    if not kernel32.SetConsoleMode(handle, original | 0x0004):
        raise AsciiDlpError(
            "ANSI表示を有効化できません。Windows Terminalを使用してください。"
        )
    return int(handle), original


def display_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
    return width


def clip_cells(text: str, maximum: int) -> str:
    output: list[str] = []
    used = 0
    for char in text:
        char_width = display_width(char)
        if used + char_width > maximum:
            break
        output.append(char)
        used += char_width
    return "".join(output)


@dataclass(frozen=True)
class ScreenSize:
    columns: int
    rows: int


class TerminalScreen(AbstractContextManager["TerminalScreen"]):
    HUD_ROWS = 2

    def __init__(self) -> None:
        self._windows_mode: tuple[int, int] | None = None

    def __enter__(self) -> TerminalScreen:
        if not sys.stdout.isatty():
            raise AsciiDlpError("ASCII動画の表示には対話端末が必要です。")
        self._windows_mode = _enable_windows_vt()
        self._write("\x1b[?1049h\x1b[?25l\x1b[2J\x1b[H")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._write("\x1b[0m\x1b[?25h\x1b[?1049l")
        if self._windows_mode is not None:
            import ctypes

            handle, original = self._windows_mode
            ctypes.windll.kernel32.SetConsoleMode(handle, original)

    @staticmethod
    def _write(text: str) -> None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except UnicodeEncodeError:
            encoding = sys.stdout.encoding or "utf-8"
            sys.stdout.buffer.write(text.encode(encoding, errors="replace"))
            sys.stdout.buffer.flush()

    @staticmethod
    def size() -> ScreenSize:
        terminal = shutil.get_terminal_size((80, 24))
        return ScreenSize(max(1, terminal.columns), max(1, terminal.lines))

    def render(
        self,
        lines: list[str],
        status: str,
        *,
        paused: bool,
        footer: str | None = None,
    ) -> None:
        size = self.size()
        usable_width = max(1, size.columns - 1)
        video_rows = max(1, size.rows - self.HUD_ROWS)
        top = max(0, (video_rows - len(lines)) // 2)
        canvas: list[str] = [""] * top
        for line in lines[:video_rows]:
            clipped = line[:usable_width]
            left = max(0, (usable_width - len(clipped)) // 2)
            canvas.append(" " * left + clipped)
        canvas.extend([""] * max(0, video_rows - len(canvas)))

        state = "PAUSE" if paused else "PLAY "
        canvas.append(clip_cells(f"[{state}] {status}", usable_width))
        controls = footer or "Space:停止/再開  ←/→:±5秒  ↑/↓:±30秒  Q:終了"
        canvas.append(clip_cells(controls, usable_width))
        payload = "\x1b[H" + "\n".join(f"\x1b[2K{line}" for line in canvas[: size.rows])
        self._write(payload)


def wait_with_input(reader: KeyReader, seconds: float) -> list[Key]:
    deadline = time.monotonic() + max(0.0, seconds)
    events: list[Key] = []
    while time.monotonic() < deadline:
        events.extend(reader.poll())
        if events:
            break
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    return events
