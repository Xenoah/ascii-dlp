import unittest

from ascii_dlp.terminal import Key, KeyReader


class KeyReaderTests(unittest.TestCase):
    def test_unknown_csi_does_not_trigger_letter_shortcut(self) -> None:
        reader = KeyReader()
        reader._buffer = b"\x1b[1;5D"
        self.assertEqual(reader._consume_buffer(), [])
        self.assertEqual(reader._buffer, b"")

    def test_home_sequence_does_not_trigger_h_shortcut(self) -> None:
        reader = KeyReader()
        reader._buffer = b"\x1b[H"
        self.assertEqual(reader._consume_buffer(), [])

    def test_split_arrow_sequence_is_retained(self) -> None:
        reader = KeyReader()
        reader._buffer = b"\x1b["
        self.assertEqual(reader._consume_buffer(), [])
        reader._buffer += b"D"
        self.assertEqual(reader._consume_buffer(), [Key.BACK])

    def test_escape_does_not_stall_following_q(self) -> None:
        reader = KeyReader()
        reader._buffer = b"\x1b"
        self.assertEqual(reader._consume_buffer(), [])
        reader._buffer += b"q"
        self.assertEqual(reader._consume_buffer(), [Key.QUIT])


if __name__ == "__main__":
    unittest.main()
