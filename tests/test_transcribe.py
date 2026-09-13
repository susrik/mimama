"""Tests for mimama.core.transcribe (subprocess calls are faked)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from mimama.config import Config
from mimama.core.transcribe import (
    DONE,
    TranscriptionJob,
    TranscriptionJobRegistry,
    run_transcription_pipeline,
)


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


def _fake_run_factory(write_txt: bool = True, ffmpeg_rc: int = 0, whisper_rc: int = 0):
    def _fake_run(cmd: list[str], cwd: Any = None, **kwargs: Any) -> _FakeCompleted:
        if cmd[0] == "ffmpeg":
            # simulate ffmpeg by writing the wav output file
            out = Path(cmd[-1])
            if ffmpeg_rc == 0:
                out.write_bytes(b"fake-wav")
            return _FakeCompleted(returncode=ffmpeg_rc, stderr="ffmpeg boom" if ffmpeg_rc else "")
        if "whisper" in cmd[0]:
            f_index = cmd.index("-f")
            audio_path = Path(cmd[f_index + 1])
            if whisper_rc == 0 and write_txt:
                (Path(cwd) / f"{audio_path.stem}.txt").write_text(
                    "hello transcript", encoding="utf-8"
                )
            return _FakeCompleted(
                returncode=whisper_rc, stderr="whisper boom" if whisper_rc else ""
            )
        raise AssertionError(f"unexpected command: {cmd}")

    return _fake_run


class TestRunTranscriptionPipelineWav:
    def test_wav_direct(
        self,
        config: Config,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audio = tmp_path / "meeting.wav"
        audio.write_bytes(b"fake-wav")
        monkeypatch.setattr(subprocess, "run", _fake_run_factory())
        result = run_transcription_pipeline(config, str(audio))
        assert result == "hello transcript"

    def test_missing_file(
        self,
        config: Config,
        tmp_path: Path
    ) -> None:
        with pytest.raises(FileNotFoundError):
            run_transcription_pipeline(config, str(tmp_path / "nope.wav"))

    def test_unsupported_format(
        self,
        config: Config,
        tmp_path: Path
    ) -> None:
        audio = tmp_path / "meeting.mp3"
        audio.write_bytes(b"x")
        with pytest.raises(RuntimeError, match="unsupported audio format"):
            run_transcription_pipeline(config, str(audio))


class TestRunTranscriptionPipelineM4a:
    def test_converts_then_transcribes(
        self,
        config: Config,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audio = tmp_path / "meeting.m4a"
        audio.write_bytes(b"fake-m4a")
        monkeypatch.setattr(subprocess, "run", _fake_run_factory())
        result = run_transcription_pipeline(config, str(audio))
        assert result == "hello transcript"

    def test_ffmpeg_failure(
        self,
        config: Config,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audio = tmp_path / "meeting.m4a"
        audio.write_bytes(b"fake-m4a")
        monkeypatch.setattr(subprocess, "run", _fake_run_factory(ffmpeg_rc=1))
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            run_transcription_pipeline(config, str(audio))

    def test_whisper_failure(
        self,
        config: Config,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audio = tmp_path / "meeting.m4a"
        audio.write_bytes(b"fake-m4a")
        monkeypatch.setattr(subprocess, "run", _fake_run_factory(whisper_rc=1))
        with pytest.raises(RuntimeError, match="whisper-cli failed"):
            run_transcription_pipeline(config, str(audio))

    def test_missing_output_file(
        self,
        config: Config,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audio = tmp_path / "meeting.m4a"
        audio.write_bytes(b"fake-m4a")
        monkeypatch.setattr(subprocess, "run", _fake_run_factory(write_txt=False))
        with pytest.raises(RuntimeError, match="did not produce"):
            run_transcription_pipeline(config, str(audio))


def _fake_popen_factory(write_txt: bool = True, ffmpeg_rc: int = 0, whisper_rc: int = 0):
    class _FakeProcess:
        def __init__(self, cmd: list[str], cwd: Any, **kwargs: Any) -> None:
            self.cmd = cmd
            self.cwd = cwd
            if cmd[0] == "ffmpeg":
                out = Path(cmd[-1])
                if ffmpeg_rc == 0:
                    out.write_bytes(b"fake-wav")
                self.stdout = iter(["converting\n", "done\n"])
                self._rc = ffmpeg_rc
            else:
                f_index = cmd.index("-f")
                audio_path = Path(cmd[f_index + 1])
                if whisper_rc == 0 and write_txt:
                    (Path(cwd) / f"{audio_path.stem}.txt").write_text(
                        "streamed transcript", encoding="utf-8"
                    )
                self.stdout = iter(["loading model\n", "transcribing\n"])
                self._rc = whisper_rc

        def wait(self) -> int:
            return self._rc

    def _fake_popen(cmd: list[str], cwd: Any = None, **kwargs: Any) -> _FakeProcess:
        return _FakeProcess(cmd, cwd, **kwargs)

    return _fake_popen


class TestTranscriptionJob:
    def test_wav_upload_success(
        self,
        config: Config,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(subprocess, "Popen", _fake_popen_factory())
        job = TranscriptionJob(config=config, filename="meeting.wav", audio_bytes=b"fake-wav")
        job._run()  # run synchronously for a deterministic test
        assert job.transcript == "streamed transcript"
        assert job.error is None
        lines = list(iter(job.lines.get, DONE))
        assert any("whisper" in line for line in lines)

    def test_m4a_upload_runs_ffmpeg_then_whisper(
        self,
        config: Config,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(subprocess, "Popen", _fake_popen_factory())
        job = TranscriptionJob(config=config, filename="meeting.m4a", audio_bytes=b"fake-m4a")
        job._run()
        assert job.transcript == "streamed transcript"
        lines = list(iter(job.lines.get, DONE))
        assert any("ffmpeg" in line for line in lines)
        assert any("whisper" in line for line in lines)

    def test_unsupported_format(
        self,
        config: Config,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job = TranscriptionJob(config=config, filename="meeting.mp3", audio_bytes=b"x")
        job._run()
        assert job.transcript is None
        assert job.error is not None
        assert "unsupported audio format" in job.error

    def test_ffmpeg_failure_sets_error(
        self,
        config: Config,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(subprocess, "Popen", _fake_popen_factory(ffmpeg_rc=1))
        job = TranscriptionJob(config=config, filename="meeting.m4a", audio_bytes=b"x")
        job._run()
        assert job.transcript is None
        assert "ffmpeg failed" in (job.error or "")

    def test_whisper_failure_sets_error(
        self,
        config: Config,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(subprocess, "Popen", _fake_popen_factory(whisper_rc=1))
        job = TranscriptionJob(config=config, filename="meeting.wav", audio_bytes=b"x")
        job._run()
        assert "whisper-cli failed" in (job.error or "")

    def test_start_runs_in_background_thread(
        self,
        config: Config,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(subprocess, "Popen", _fake_popen_factory())
        job = TranscriptionJob(config=config, filename="meeting.wav", audio_bytes=b"x")
        job.start()
        # drain the queue until the sentinel; this blocks until the thread finishes
        list(iter(job.lines.get, DONE))
        assert job.transcript == "streamed transcript"


class TestTranscriptionJobRegistry:
    def test_create_get_discard(
        self,
        config: Config,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(subprocess, "Popen", _fake_popen_factory())
        registry = TranscriptionJobRegistry()
        job = registry.create(config, "meeting.wav", b"x")
        assert registry.get(job.id) is job
        list(iter(job.lines.get, DONE))  # wait for completion
        registry.discard(job.id)
        assert registry.get(job.id) is None

    def test_get_missing_returns_none(
        self,
        config: Config
    ) -> None:
        registry = TranscriptionJobRegistry()
        assert registry.get("nope") is None
