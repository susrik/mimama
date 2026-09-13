"""Audio transcription pipeline with ffmpeg and whisper support.

Handles conversion of m4a to wav files and running whisper inference.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mimama.config import Config

def _expand_path(path: str) -> str:
    """Expand shell variables like ~ in paths."""
    return os.path.expandvars(path)

def run_transcription_pipeline(
    config: Config,
    audio_file: str,
    model_path: str | None = None
) -> str:
    """
    Run transcription pipeline on an audio file.
    
    Args:
        config: Application configuration
        audio_file: Path to input audio file (.m4a or .wav)
        model_path: Optional path to whisper model
    
    Returns:
        Transcribed text from the audio file
    """
    # Create a temporary directory for processing
    with tempfile.TemporaryDirectory() as temp_dir:
        audio_path = Path(audio_file)
        temp_audio_path = Path(temp_dir) / audio_path.name

        # Copy the original audio file to temp directory
        if audio_path.exists():
            import shutil
            shutil.copy2(audio_path, temp_audio_path)
        else:
            raise FileNotFoundError(f"Audio file not found: {audio_file}")

        # If it's an m4a file, convert to wav first
        if audio_path.suffix.lower() == ".m4a":
            wav_file = temp_dir / f"{audio_path.stem}.wav"
            ffmpeg_cmd = [
                config.transcripts.ffmpeg,
                "-i", str(temp_audio_path),
                "-ar", "16000",
                "-ac", "1",
                "-c:a", "pcm_s16le",
                str(wav_file)
            ]

            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg failed: {result.stderr}")

            # Use the wav file for whisper
            audio_path = wav_file

        # Run whisper inference
        whisper_cmd = [config.transcripts.whisper.cli]

        if model_path:
            # Expand path with shell variables like ~
            expanded_model_path = _expand_path(model_path)
            whisper_cmd.extend(["-m", expanded_model_path])

        whisper_cmd.extend([
            "-f", str(audio_path),
            "-otxt"
        ])

        result = subprocess.run(whisper_cmd, capture_output=True, text=True, check=False, cwd=temp_dir)
        if result.returncode != 0:
            raise RuntimeError(f"Whisper failed: {result.stderr}")

        # Read the output file
        txt_file = temp_dir / f"{audio_path.stem}.txt"
        if txt_file.exists():
            return txt_file.read_text(encoding="utf-8")
        else:
            raise RuntimeError("Whisper did not produce expected output file")
