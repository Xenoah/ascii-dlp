import io
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from ascii_dlp.player import AudioPipeline, _pcm_stream, format_time


class FormatTimeTests(unittest.TestCase):
    def test_minutes(self) -> None:
        self.assertEqual(format_time(65.9), "01:05")

    def test_hours(self) -> None:
        self.assertEqual(format_time(3661), "1:01:01")

    def test_live(self) -> None:
        self.assertEqual(format_time(None), "LIVE")


class AudioPipelineTests(unittest.TestCase):
    def test_pcm_stream_reads_requested_frames(self) -> None:
        stream = _pcm_stream(io.BytesIO(b"abcdefghij"))

        self.assertEqual(next(stream), b"")
        self.assertEqual(stream.send(2), b"abcdefgh")
        self.assertEqual(stream.send(2), b"ij")
        with self.assertRaises(StopIteration):
            stream.send(1)

    def test_audio_uses_ffmpeg_and_miniaudio(self) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = io.BytesIO(b"\0" * 64)
                self.returncode = None

            def poll(self) -> int | None:
                return self.returncode

            def terminate(self) -> None:
                self.returncode = -15

            def wait(self, timeout: float) -> int:
                del timeout
                return self.returncode or 0

        class FakeDevice:
            def __init__(self, **options: object) -> None:
                self.options = options
                self.stream = None
                self.closed = False

            def start(self, stream: object) -> None:
                self.stream = stream

            def close(self) -> None:
                self.closed = True

        process = FakeProcess()
        devices: list[FakeDevice] = []

        def create_device(**options: object) -> FakeDevice:
            device = FakeDevice(**options)
            devices.append(device)
            return device

        miniaudio = SimpleNamespace(
            PlaybackDevice=create_device,
            SampleFormat=SimpleNamespace(SIGNED16="signed16"),
        )
        source = SimpleNamespace(location="video", input_options=())
        toolchain = SimpleNamespace(ffmpeg="ffmpeg")

        with (
            mock.patch.dict(sys.modules, {"miniaudio": miniaudio}),
            mock.patch("ascii_dlp.player.subprocess.Popen", return_value=process) as popen,
        ):
            audio = AudioPipeline(source, toolchain, position=1.5, volume=80)

        device = devices[0]
        command = popen.call_args.args[0]
        filter_index = command.index("-af")
        self.assertEqual(command[filter_index + 1], "volume=0.8000")
        self.assertEqual(device.options["output_format"], "signed16")
        self.assertEqual(device.options["nchannels"], 2)
        self.assertEqual(device.options["sample_rate"], 48000)
        self.assertIsNotNone(device.stream)

        audio.stop()
        self.assertTrue(device.closed)
        self.assertTrue(process.stdout.closed)


if __name__ == "__main__":
    unittest.main()
