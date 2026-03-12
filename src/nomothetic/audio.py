"""Audio recording and playback for the nomon fleet.

Uses the USB microphone (C-Media/PCM2902, ALSA card 2 on PicarX) for
recording and the HifiBerry DAC (ALSA card 1) for playback.  The speaker
amplifier is enabled via the nomopractic HAT daemon before playback and
disabled after.

Classes
-------
AudioRecorder
    Records audio from the USB microphone to WAV files.
AudioPlayer
    Plays back audio files over the HifiBerry DAC.
AudioStatus
    Dataclass snapshot of current recorder/player state.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

try:
    import pyaudio  # type: ignore[import-untyped]

    _PYAUDIO_AVAILABLE = True
except ImportError:  # pragma: no cover — pyaudio not installed in dev env
    pyaudio = None  # type: ignore[assignment]
    _PYAUDIO_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default directory where recorded audio files are saved.
#: Resolved from ``NOMON_MEDIA_DIR/audio`` (with tilde expansion).
DEFAULT_AUDIO_DIR: str = str(
    Path(os.environ.get("NOMON_MEDIA_DIR", "~/perceptua-nomon/media")).expanduser() / "audio"
)

#: ALSA card index for the USB microphone (PCM2902 on PicarX).
DEFAULT_INPUT_DEVICE_INDEX: int = int(os.environ.get("NOMON_AUDIO_INPUT_INDEX", "2"))

#: Recording sample rate (Hz).
RECORD_RATE: int = 44100

#: Recording channel count.
RECORD_CHANNELS: int = 1

#: PyAudio sample format for recording.
RECORD_FORMAT_NAME: str = "paInt16"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AudioStatus:
    """Snapshot of current audio recorder and player state.

    Attributes
    ----------
    recording : bool
        True if a recording session is active.
    recording_file : str | None
        Path of the file currently being recorded.
    playing : bool
        True if a playback session is active.
    playback_file : str | None
        Path of the file currently being played.
    """

    recording: bool = False
    recording_file: str | None = None
    playing: bool = False
    playback_file: str | None = None


# ---------------------------------------------------------------------------
# AudioRecorder
# ---------------------------------------------------------------------------


class AudioRecorder:
    """Records audio from the USB microphone to WAV files.

    Parameters
    ----------
    audio_dir : str | Path | None
        Directory where recorded WAV files are saved.  Defaults to
        ``NOMON_MEDIA_DIR`` env var / ``~/perceptua-nomon/media/audio``.
    input_device_index : int
        PyAudio device index for the USB microphone.
    """

    def __init__(
        self,
        audio_dir: str | Path | None = None,
        input_device_index: int = DEFAULT_INPUT_DEVICE_INDEX,
    ) -> None:
        self._audio_dir = Path(audio_dir or DEFAULT_AUDIO_DIR)
        self._input_device_index = input_device_index
        self._lock = threading.Lock()
        self._recording = False
        self._current_file: str | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def is_recording(self) -> bool:
        """Return True if a recording session is active."""
        return self._recording

    @property
    def current_file(self) -> str | None:
        """Path of the file currently being recorded, or None."""
        return self._current_file

    def start(self, filename: str | None = None) -> str:
        """Start recording audio to a WAV file.

        Parameters
        ----------
        filename : str | None
            Basename for the output file (without directory).  A timestamp
            name is generated when absent: ``recording_YYYYMMDD_HHMMSS.wav``.

        Returns
        -------
        str
            Absolute path of the output file.

        Raises
        ------
        RuntimeError
            If a recording is already in progress or PyAudio is unavailable.
        ValueError
            If ``filename`` contains path components, is absolute, or starts with ``'.'``.
        """
        if not _PYAUDIO_AVAILABLE:
            raise RuntimeError("pyaudio is not installed; add it with: pip install pyaudio")
        with self._lock:
            if self._recording:
                raise RuntimeError("A recording is already in progress")

            self._audio_dir.mkdir(parents=True, exist_ok=True)
            if filename is None:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                filename = f"recording_{timestamp}.wav"
            else:
                # Validate: only plain basenames are accepted from external input.
                if Path(filename).is_absolute():
                    raise ValueError(f"filename cannot be an absolute path: {filename}")
                if "/" in filename or "\\" in filename:
                    raise ValueError(f"filename cannot contain path separators: {filename}")
                if filename in ("..", "."):
                    raise ValueError(f"filename cannot be '{filename}'")
                if filename.startswith("."):
                    raise ValueError(f"filename cannot start with '.': {filename}")
            out_path = str(self._audio_dir / filename)
            self._current_file = out_path
            self._recording = True
            self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._record_loop,
            args=(out_path,),
            daemon=True,
            name="audio-recorder",
        )
        self._thread.start()
        return out_path

    def stop(self) -> str | None:
        """Stop the active recording session.

        Returns
        -------
        str | None
            Path of the completed recording, or None if no recording was active.
        """
        with self._lock:
            if not self._recording:
                return None
            path = self._current_file

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        with self._lock:
            self._recording = False
            self._current_file = None
        return path

    def _record_loop(self, out_path: str) -> None:
        """Background thread: capture audio chunks and write WAV file."""
        chunk = 1024
        fmt = getattr(pyaudio, RECORD_FORMAT_NAME)
        pa = pyaudio.PyAudio()
        frames: list[bytes] = []
        try:
            stream = pa.open(
                format=fmt,
                channels=RECORD_CHANNELS,
                rate=RECORD_RATE,
                input=True,
                input_device_index=self._input_device_index,
                frames_per_buffer=chunk,
            )
            while not self._stop_event.is_set():
                data = stream.read(chunk, exception_on_overflow=False)
                frames.append(data)
            stream.stop_stream()
            stream.close()
        except OSError as e:
            logger.error("Audio recording stream error: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.error("Unexpected error in audio recording thread: %s", e)
        finally:
            pa.terminate()

        if not frames:
            logger.warning("No audio frames captured; skipping WAV write for %s", out_path)
            return

        # Write collected frames to WAV.
        try:
            with wave.open(out_path, "wb") as wf:
                wf.setnchannels(RECORD_CHANNELS)
                wf.setsampwidth(2)  # 16-bit = 2 bytes
                wf.setframerate(RECORD_RATE)
                wf.writeframes(b"".join(frames))
        except OSError as e:
            logger.error("Failed to write WAV file %s: %s", out_path, e)


# ---------------------------------------------------------------------------
# AudioPlayer
# ---------------------------------------------------------------------------


class AudioPlayer:
    """Plays back WAV audio files via the HifiBerry DAC.

    Parameters
    ----------
    audio_dir : str | Path | None
        Directory searched when a bare filename (no directory component) is
        given to :meth:`play`.
    output_device_index : int | None
        PyAudio device index for the DAC output.  ``None`` uses the system
        default output device.
    """

    def __init__(
        self,
        audio_dir: str | Path | None = None,
        output_device_index: int | None = None,
    ) -> None:
        self._audio_dir = Path(audio_dir or DEFAULT_AUDIO_DIR)
        self._output_device_index = output_device_index
        self._lock = threading.Lock()
        self._playing = False
        self._current_file: str | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def is_playing(self) -> bool:
        """Return True if a playback session is active."""
        return self._playing

    @property
    def current_file(self) -> str | None:
        """Path of the file currently being played, or None."""
        return self._current_file

    def play(self, filename: str) -> None:
        """Start playing a WAV file in the background.

        Parameters
        ----------
        filename : str
            Path to the WAV file.  A bare name (no directory component) is
            resolved against ``audio_dir``.

        Raises
        ------
        RuntimeError
            If playback is already in progress or PyAudio is unavailable.
        FileNotFoundError
            If the file does not exist.
        ValueError
            If ``filename`` is not a bare name (contains path components or is absolute).
        """
        if not _PYAUDIO_AVAILABLE:
            raise RuntimeError("pyaudio is not installed; add it with: pip install pyaudio")

        # Enforce project rule: only accept bare filenames from external input.
        # Reject absolute paths and any string with directory components.
        raw_path = Path(filename)
        if raw_path.is_absolute() or raw_path.name != filename:
            raise ValueError("filename must be a bare name without directory components")

        audio_root = self._audio_dir.resolve()
        path = (audio_root / raw_path.name).resolve()
        try:
            # Ensure the resolved path is still within the configured audio directory.
            path.relative_to(audio_root)
        except ValueError as e:
            raise ValueError("resolved audio file path escapes audio_dir") from e
        if not path.exists():
            raise FileNotFoundError(f"audio file not found: {path}")

        with self._lock:
            if self._playing:
                raise RuntimeError("Playback is already in progress")
            self._current_file = str(path)
            self._playing = True
            self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._play_loop,
            args=(str(path),),
            daemon=True,
            name="audio-player",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop ongoing playback."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        with self._lock:
            self._playing = False
            self._current_file = None

    def _play_loop(self, path: str) -> None:
        """Background thread: read WAV chunks and send to PyAudio output."""
        chunk = 1024
        pa = pyaudio.PyAudio()
        try:
            with wave.open(path, "rb") as wf:
                stream = pa.open(
                    format=pa.get_format_from_width(wf.getsampwidth()),
                    channels=wf.getnchannels(),
                    rate=wf.getframerate(),
                    output=True,
                    output_device_index=self._output_device_index,
                )
                data = wf.readframes(chunk)
                while data and not self._stop_event.is_set():
                    stream.write(data)
                    data = wf.readframes(chunk)
                stream.stop_stream()
                stream.close()
        except Exception as e:  # noqa: BLE001
            logger.error("Audio playback error: %s", e)
        finally:
            pa.terminate()
            with self._lock:
                self._playing = False
                self._current_file = None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def list_audio_files(audio_dir: str | Path | None = None) -> list[str]:
    """Return sorted list of WAV file basenames in ``audio_dir``.

    Parameters
    ----------
    audio_dir : str | Path | None
        Directory to search.  Defaults to ``DEFAULT_AUDIO_DIR``.

    Returns
    -------
    list[str]
        Sorted list of ``*.wav`` basenames.
    """
    d = Path(audio_dir or DEFAULT_AUDIO_DIR)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.wav"))
