"""Audio transcription: ffmpeg (any format → wav) + whisper-cli, with two
entry points.

- :func:`run_transcription_pipeline` — synchronous, path-based (used by the
  stable ``/api/transcribe`` JSON endpoint: CLI, MCP, and any external
  client that shares a filesystem with the server).
- :class:`TranscriptionJob` — asynchronous, upload-based (used by the ``/gui``
  "new file" flow): takes raw uploaded bytes, runs ffmpeg/whisper in a
  background thread, and streams each subprocess output line to a queue so
  the GUI can show real-time console output over SSE.

Both run the same two external commands in a fresh temp directory. Input
already in ``.wav`` format is passed straight to whisper-cli; any other
format is first handed to ffmpeg, which decodes whatever it recognizes (the
intermediate filename is arbitrary — it lives only in the temp directory):

    ffmpeg -i <input> -ar 16000 -ac 1 -c:a pcm_s16le <tmp>/converted.wav
    whisper-cli -mc 0 -m <model> -f <tmp>/converted.wav -otxt -of <tmp>/converted

If ffmpeg can't decode the input, its stderr is surfaced as the error (via
the raised exception for the API path, or the streamed console output for
the GUI job) rather than us pre-validating file extensions.

Note on ``-of``: whisper-cli's default output naming appends ``.txt`` to the
*full* input filename (e.g. ``<file>.wav.txt``), not to its stem. We always
pass ``-of`` explicitly so the resulting ``.txt`` path is known exactly,
rather than depending on that (version-specific) default naming convention.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mimama.config import Config

# sentinel put on a job's queue once no more output lines will follow
DONE = object()


def _expand_path(path: str) -> str:
    """Expand shell variables and ``~`` in paths."""
    return os.path.expanduser(os.path.expandvars(path))


def _whisper_command(
    config: Config, model_path: str | None, audio_path: Path, output_stem: Path
) -> list[str]:
    """Build the whisper-cli command.

    ``output_stem`` is passed via ``-of`` (output file path, without
    extension) so we know exactly where the ``.txt`` transcript will land,
    rather than relying on whisper-cli's default naming convention (which
    appends ``.txt`` to the *full* input filename, e.g. ``foo.wav.txt`` —
    not ``foo.txt``).
    """
    model = (
        _expand_path(model_path) if model_path else _expand_path(config.transcripts.whisper.model)
    )
    return [
        config.transcripts.whisper.cli,
        "-mc", "0",
        "-m", model,
        "-f", str(audio_path),
        "-otxt",
        "-of", str(output_stem),
    ]


def _ffmpeg_command(config: Config, audio_path: Path, wav_path: Path) -> list[str]:
    return [
        config.transcripts.ffmpeg,
        "-i", str(audio_path),
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(wav_path),
    ]


def run_transcription_pipeline(
    config: Config,
    audio_file: str,
    model_path: str | None = None,
) -> str:
    """Transcribe a server-local audio file path. Used by ``/api/transcribe``.

    Args:
        config: Application configuration.
        audio_file: Path to input audio file, any format ffmpeg can decode.
        model_path: Optional override for the whisper model path.

    Returns:
        Transcribed text.
    """
    audio_path = Path(_expand_path(audio_file))
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_file}")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)

        if audio_path.suffix.lower() == ".wav":
            working_audio = temp_dir_path / audio_path.name
            shutil.copy2(audio_path, working_audio)
        else:
            working_audio = temp_dir_path / "converted.wav"
            result = subprocess.run(
                _ffmpeg_command(config, audio_path, working_audio),
                capture_output=True, text=True, check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {result.stderr}")

        result = subprocess.run(
            _whisper_command(
                config, model_path, working_audio, temp_dir_path / working_audio.stem
            ),
            capture_output=True, text=True, check=False, cwd=temp_dir,
        )
        if result.returncode != 0:
            raise RuntimeError(f"whisper-cli failed: {result.stderr}")

        txt_file = temp_dir_path / f"{working_audio.stem}.txt"
        if not txt_file.is_file():
            raise RuntimeError("whisper-cli did not produce the expected output file")
        return txt_file.read_text(encoding="utf-8")


@dataclass
class TranscriptionJob:
    """A background ffmpeg→whisper run over an uploaded audio file.

    ``lines`` is a queue of console output strings, terminated by a final
    ``DONE`` sentinel once ``transcript`` or ``error`` is set.
    """

    config: Config
    filename: str
    audio_bytes: bytes
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    lines: queue.Queue[object] = field(default_factory=queue.Queue)
    transcript: str | None = None
    error: str | None = None

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _emit(self, line: str) -> None:
        self.lines.put(line)

    def _run_streamed(self, cmd: list[str], cwd: Path) -> int:
        self._emit("$ " + " ".join(cmd))
        process = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            self._emit(line.rstrip("\n"))
        return process.wait()

    def _run(self) -> None:
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_dir_path = Path(temp_dir)
                uploaded_path = temp_dir_path / self.filename
                uploaded_path.write_bytes(self.audio_bytes)

                if uploaded_path.suffix.lower() == ".wav":
                    audio_path = uploaded_path
                else:
                    audio_path = temp_dir_path / "converted.wav"
                    rc = self._run_streamed(
                        _ffmpeg_command(self.config, uploaded_path, audio_path), temp_dir_path
                    )
                    if rc != 0:
                        raise RuntimeError("ffmpeg failed (see console output)")

                rc = self._run_streamed(
                    _whisper_command(
                        self.config, None, audio_path, temp_dir_path / audio_path.stem
                    ),
                    temp_dir_path,
                )
                if rc != 0:
                    raise RuntimeError("whisper-cli failed (see console output)")

                txt_path = temp_dir_path / f"{audio_path.stem}.txt"
                if not txt_path.is_file():
                    raise RuntimeError("whisper-cli did not produce the expected output file")
                self.transcript = txt_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - surfaced to the GUI, not swallowed
            self.error = str(exc)
            self._emit(f"ERROR: {exc}")
        finally:
            self.lines.put(DONE)


class TranscriptionJobRegistry:
    """In-memory registry of transcription jobs (single-process, trusted-network tool)."""

    def __init__(self) -> None:
        self._jobs: dict[str, TranscriptionJob] = {}

    def create(self, config: Config, filename: str, audio_bytes: bytes) -> TranscriptionJob:
        job = TranscriptionJob(config=config, filename=filename, audio_bytes=audio_bytes)
        self._jobs[job.id] = job
        job.start()
        return job

    def get(self, job_id: str) -> TranscriptionJob | None:
        return self._jobs.get(job_id)

    def discard(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
