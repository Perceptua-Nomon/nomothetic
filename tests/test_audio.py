"""Tests for the nomothetic.audio module.

These tests do not require real hardware or PyAudio — pyaudio is mocked.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nomothetic.audio import (
    AudioPlayer,
    AudioRecorder,
    list_audio_files,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_dummy_wav(path: Path, duration_s: float = 0.1, rate: int = 44100) -> None:
    """Write a minimal valid WAV file for testing."""
    n_frames = int(rate * duration_s)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{n_frames}h", *([0] * n_frames)))


# ---------------------------------------------------------------------------
# list_audio_files()
# ---------------------------------------------------------------------------


def test_list_audio_files_empty_dir(tmp_path):
    """`list_audio_files` returns [] for an empty directory."""
    assert list_audio_files(tmp_path) == []


def test_list_audio_files_missing_dir(tmp_path):
    """`list_audio_files` returns [] when the directory does not exist."""
    assert list_audio_files(tmp_path / "nonexistent") == []


def test_list_audio_files_returns_sorted_wav_names(tmp_path):
    """`list_audio_files` returns sorted WAV basenames."""
    for name in ("c.wav", "a.wav", "b.wav"):
        (tmp_path / name).touch()
    (tmp_path / "image.jpg").touch()  # should be excluded
    result = list_audio_files(tmp_path)
    assert result == ["a.wav", "b.wav", "c.wav"]


# ---------------------------------------------------------------------------
# AudioRecorder
# ---------------------------------------------------------------------------


class TestAudioRecorder:
    def test_is_not_recording_initially(self, tmp_path):
        recorder = AudioRecorder(audio_dir=tmp_path)
        assert recorder.is_recording is False
        assert recorder.current_file is None

    def test_start_raises_when_already_recording(self, tmp_path):
        """Starting a second recording raises RuntimeError."""
        MockPyAudio = MagicMock()
        mock_stream = MagicMock()
        MockPyAudio.return_value.open.return_value = mock_stream
        mock_stream.read.return_value = b"\x00" * 2048

        with (
            patch("nomothetic.audio._PYAUDIO_AVAILABLE", True),
            patch("nomothetic.audio.pyaudio") as mock_pa_module,
        ):
            mock_pa_module.PyAudio = MockPyAudio
            mock_pa_module.paInt16 = 8

            recorder = AudioRecorder(audio_dir=tmp_path)
            recorder.start()
            assert recorder.is_recording
            with pytest.raises(RuntimeError, match="already in progress"):
                recorder.start()
            recorder.stop()

    def test_start_without_pyaudio_raises(self, tmp_path):
        """RuntimeError raised when pyaudio is not installed."""
        with patch("nomothetic.audio._PYAUDIO_AVAILABLE", False):
            recorder = AudioRecorder(audio_dir=tmp_path)
            with pytest.raises(RuntimeError, match="pyaudio"):
                recorder.start()

    def test_start_rejects_absolute_path(self, tmp_path):
        """ValueError raised when filename is an absolute path."""
        with patch("nomothetic.audio._PYAUDIO_AVAILABLE", True):
            recorder = AudioRecorder(audio_dir=tmp_path)
            with pytest.raises(ValueError, match="absolute"):
                recorder.start("/etc/passwd")

    def test_start_rejects_path_traversal(self, tmp_path):
        """ValueError raised when filename contains path separators."""
        with patch("nomothetic.audio._PYAUDIO_AVAILABLE", True):
            recorder = AudioRecorder(audio_dir=tmp_path)
            with pytest.raises(ValueError, match="separator"):
                recorder.start("../escape.wav")

    def test_start_rejects_dotfile(self, tmp_path):
        """ValueError raised when filename starts with a dot."""
        with patch("nomothetic.audio._PYAUDIO_AVAILABLE", True):
            recorder = AudioRecorder(audio_dir=tmp_path)
            with pytest.raises(ValueError, match=r"\.'"):
                recorder.start(".hidden.wav")

    def test_stop_with_no_recording_returns_none(self, tmp_path):
        recorder = AudioRecorder(audio_dir=tmp_path)
        assert recorder.stop() is None

    def test_start_generates_timestamped_filename(self, tmp_path):
        """When no filename is given, a timestamped name is generated."""
        MockPyAudio = MagicMock()
        mock_stream = MagicMock()
        MockPyAudio.return_value.open.return_value = mock_stream
        mock_stream.read.return_value = b"\x00" * 2048

        with (
            patch("nomothetic.audio._PYAUDIO_AVAILABLE", True),
            patch("nomothetic.audio.pyaudio") as mock_pa_module,
        ):
            mock_pa_module.PyAudio = MockPyAudio
            mock_pa_module.paInt16 = 8

            recorder = AudioRecorder(audio_dir=tmp_path)
            path = recorder.start()
            assert "recording_" in path
            assert path.endswith(".wav")
            recorder.stop()

    def test_start_with_custom_filename(self, tmp_path):
        """Explicit filename is used as-is."""
        MockPyAudio = MagicMock()
        mock_stream = MagicMock()
        MockPyAudio.return_value.open.return_value = mock_stream
        mock_stream.read.return_value = b"\x00" * 2048

        with (
            patch("nomothetic.audio._PYAUDIO_AVAILABLE", True),
            patch("nomothetic.audio.pyaudio") as mock_pa_module,
        ):
            mock_pa_module.PyAudio = MockPyAudio
            mock_pa_module.paInt16 = 8

            recorder = AudioRecorder(audio_dir=tmp_path)
            path = recorder.start("test_clip.wav")
            assert path == str(tmp_path / "test_clip.wav")
            recorder.stop()

    def test_stop_returns_output_path(self, tmp_path):
        """stop() returns the path of the recorded file."""
        MockPyAudio = MagicMock()
        mock_stream = MagicMock()
        MockPyAudio.return_value.open.return_value = mock_stream
        mock_stream.read.return_value = b"\x00" * 2048

        with (
            patch("nomothetic.audio._PYAUDIO_AVAILABLE", True),
            patch("nomothetic.audio.pyaudio") as mock_pa_module,
        ):
            mock_pa_module.PyAudio = MockPyAudio
            mock_pa_module.paInt16 = 8

            recorder = AudioRecorder(audio_dir=tmp_path)
            out = recorder.start("out.wav")
            result = recorder.stop()
            assert result == out
            assert recorder.is_recording is False


# ---------------------------------------------------------------------------
# AudioPlayer
# ---------------------------------------------------------------------------


class TestAudioPlayer:
    def test_is_not_playing_initially(self, tmp_path):
        player = AudioPlayer(audio_dir=tmp_path)
        assert player.is_playing is False
        assert player.current_file is None

    def test_play_without_pyaudio_raises(self, tmp_path):
        _write_dummy_wav(tmp_path / "clip.wav")
        with patch("nomothetic.audio._PYAUDIO_AVAILABLE", False):
            player = AudioPlayer(audio_dir=tmp_path)
            with pytest.raises(RuntimeError, match="pyaudio"):
                player.play("clip.wav")

    def test_play_missing_file_raises(self, tmp_path):
        with patch("nomothetic.audio._PYAUDIO_AVAILABLE", True):
            player = AudioPlayer(audio_dir=tmp_path)
            with pytest.raises(FileNotFoundError):
                player.play("missing.wav")

    def test_play_while_already_playing_raises(self, tmp_path):
        _write_dummy_wav(tmp_path / "a.wav")
        _write_dummy_wav(tmp_path / "b.wav")

        mock_stream = MagicMock()
        MockPyAudio = MagicMock()
        MockPyAudio.return_value.open.return_value = mock_stream
        # readframes returns empty bytes immediately to end play loop fast
        mock_stream.read.return_value = b""

        with (
            patch("nomothetic.audio._PYAUDIO_AVAILABLE", True),
            patch("nomothetic.audio.pyaudio") as mock_pa_module,
        ):
            mock_pa_module.PyAudio = MockPyAudio
            mock_pa_module.paFloat32 = 1

            player = AudioPlayer(audio_dir=tmp_path)
            player.play("a.wav")
            with pytest.raises(RuntimeError, match="already in progress"):
                player.play("b.wav")
            player.stop()

    def test_stop_when_not_playing_is_safe(self, tmp_path):
        player = AudioPlayer(audio_dir=tmp_path)
        player.stop()  # should not raise

    def test_play_resolves_bare_filename_against_audio_dir(self, tmp_path):
        """A bare filename (no directory) is resolved against audio_dir."""
        _write_dummy_wav(tmp_path / "clip.wav")

        mock_stream = MagicMock()
        MockPyAudio = MagicMock()
        MockPyAudio.return_value.open.return_value = mock_stream
        mock_stream.read.return_value = b""

        with (
            patch("nomothetic.audio._PYAUDIO_AVAILABLE", True),
            patch("nomothetic.audio.pyaudio") as mock_pa_module,
            patch("nomothetic.audio.wave") as mock_wave,
        ):
            mock_pa_module.PyAudio = MockPyAudio
            mock_wave_file = MagicMock()
            mock_wave_file.__enter__ = lambda s: s
            mock_wave_file.__exit__ = MagicMock(return_value=False)
            mock_wave_file.getnchannels.return_value = 1
            mock_wave_file.getsampwidth.return_value = 2
            mock_wave_file.getframerate.return_value = 44100
            mock_wave_file.readframes.return_value = b""
            mock_wave.open.return_value = mock_wave_file

            player = AudioPlayer(audio_dir=tmp_path)
            player.play("clip.wav")
            # Wait for background thread to finish, then verify wave.open was
            # called with the fully-resolved path (not just the bare basename).
            if player._thread is not None:
                player._thread.join(timeout=2.0)
            mock_wave.open.assert_called_once_with(str(tmp_path / "clip.wav"), "rb")
