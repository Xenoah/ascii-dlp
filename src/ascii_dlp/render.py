from __future__ import annotations

import math

from .errors import AsciiDlpError

DEFAULT_CHARS = " .,:;irsXA253hMHGS#9B&@"


def validate_chars(chars: str) -> str:
    if len(chars) < 2:
        raise AsciiDlpError(
            "--chars には明暗2段階以上の文字を指定してください。"
        )
    if any(not 0x20 <= ord(char) <= 0x7E for char in chars):
        raise AsciiDlpError(
            "--chars には制御文字を含まない"
            "印字可能ASCII文字だけを指定してください。"
        )
    return chars


def build_lut(
    chars: str = DEFAULT_CHARS,
    *,
    invert: bool = False,
    gamma: float = 1.0,
    contrast: float = 1.1,
) -> bytes:
    chars = validate_chars(chars)
    if not math.isfinite(gamma) or gamma <= 0:
        raise AsciiDlpError(
            "--gamma は0より大きい値にしてください。"
        )
    if not math.isfinite(contrast) or contrast <= 0:
        raise AsciiDlpError("--contrast は0より大きい値にしてください。")
    gradient = chars[::-1] if invert else chars
    last = len(gradient) - 1
    table: list[int] = []
    for value in range(256):
        normalized = value / 255.0
        adjusted = (normalized - 0.5) * contrast + 0.5
        adjusted = min(1.0, max(0.0, adjusted)) ** gamma
        table.append(ord(gradient[min(last, int(adjusted * last + 0.5))]))
    return bytes(table)


def frame_to_lines(frame: bytes, width: int, height: int, lut: bytes) -> list[str]:
    expected = width * height
    if len(frame) != expected:
        raise ValueError(f"expected {expected} grayscale bytes, got {len(frame)}")
    mapped = frame.translate(lut)
    return [mapped[offset : offset + width].decode("ascii") for offset in range(0, expected, width)]


def fit_dimensions(
    source_width: int,
    source_height: int,
    available_columns: int,
    available_rows: int,
    *,
    max_width: int | None = None,
    max_height: int | None = None,
    cell_aspect: float = 2.0,
) -> tuple[int, int]:
    if source_width <= 0 or source_height <= 0:
        raise ValueError("source dimensions must be positive")
    columns = max(2, available_columns)
    rows = max(1, available_rows)
    if max_width:
        columns = min(columns, max(2, max_width))
    if max_height:
        rows = min(rows, max(1, max_height))

    source_aspect = source_width / source_height
    if columns / (rows * cell_aspect) > source_aspect:
        height = rows
        width = max(2, math.floor(source_aspect * height * cell_aspect))
    else:
        width = columns
        height = max(1, math.floor(width / (source_aspect * cell_aspect)))
    return min(width, columns), min(height, rows)
