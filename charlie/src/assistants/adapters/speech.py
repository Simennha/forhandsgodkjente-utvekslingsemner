"""Speech adapters: local speech-to-text (faster-whisper) and text-to-speech (Piper).

Both real implementations run locally - audio never leaves the mini-PC.
Simulators treat audio bytes as UTF-8 text, and count calls so that tests can prove that
no audio processing happens in open-workshop mode.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class SpeechToText(Protocol):
    def transcribe(self, audio: bytes, language: str = "no") -> str: ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> bytes: ...


class FasterWhisperSTT:
    """faster-whisper running locally on CPU. NOT verified in this repository."""

    def __init__(self, model_size: str = "small", device: str = "cpu") -> None:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]  # noqa: PLC0415

        self._model: Any = WhisperModel(model_size, device=device, compute_type="int8")

    def transcribe(self, audio: bytes, language: str = "no") -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav") as fh:
            fh.write(audio)
            fh.flush()
            segments, _info = self._model.transcribe(fh.name, language=language)
            return " ".join(str(s.text).strip() for s in segments)


class PiperTTS:
    """Piper CLI with a Norwegian voice (e.g. no_NO-talesyntese-medium). NOT verified."""

    def __init__(self, voice_model: Path, binary: str = "piper") -> None:
        resolved = shutil.which(binary)
        if resolved is None:
            raise FileNotFoundError(binary)
        self._binary = resolved
        self._voice = voice_model

    def synthesize(self, text: str) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".wav") as out:
            subprocess.run(  # noqa: S603 - fixed binary, text passed on stdin
                [self._binary, "--model", str(self._voice), "--output_file", out.name],
                input=text.encode(),
                check=True,
                timeout=30,
            )
            return Path(out.name).read_bytes()


@dataclass
class SimSTT:
    calls: int = 0

    def transcribe(self, audio: bytes, language: str = "no") -> str:
        self.calls += 1
        return audio.decode("utf-8")


@dataclass
class SimTTS:
    spoken: list[str] = field(default_factory=list)

    def synthesize(self, text: str) -> bytes:
        self.spoken.append(text)
        return text.encode("utf-8")
