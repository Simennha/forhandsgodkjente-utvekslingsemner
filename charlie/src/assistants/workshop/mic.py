"""Workshop microphone pipeline (simulated hardware).

    relay (physical) -> wake word ("Charlie") -> speech-to-text of ONE utterance -> Charlie

Guards, in order, all in code:
1. Open-workshop mode or relay not connected: return immediately. Nothing is analysed -
   no wake-word detection, no speech-to-text, no model call.
2. No wake word: the frame is dropped, nothing is stored.
3. After the wake word, only that single utterance is transcribed, in memory only. It is
   never stored as a transcript: transcription is for phone calls only, and there is no
   continuous ("ambient") transcription anywhere.

A voice in the workshop is not authenticated. In normal mode it counts as a known number
(levels 1-2 at most); in guest mode only harmless level-1 answers are possible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from assistants.adapters.speech import SpeechToText, TextToSpeech
from assistants.audit import AuditLog
from assistants.events import Trust
from assistants.executor.core import CallContext
from assistants.workshop.modes import WorkshopModeController


class WakeWordDetector(Protocol):
    def detect(self, frame: bytes) -> bool: ...


@dataclass
class SimWakeWord:
    """Simulated keyword spotter (on device in production, e.g. openWakeWord)."""

    words: tuple[str, ...] = ("charlie",)
    calls: int = 0

    def detect(self, frame: bytes) -> bool:
        self.calls += 1
        text = frame.decode("utf-8", errors="ignore").strip().lower()
        return any(text.startswith(w) for w in self.words)


@dataclass
class MicPipeline:
    modes: WorkshopModeController
    wake: WakeWordDetector
    stt: SpeechToText
    tts: TextToSpeech
    audit: AuditLog
    charlie: Callable[[str, CallContext], str]
    charlie_available: Callable[[], bool]
    replies: list[str] = field(default_factory=list)

    def process(self, frame: bytes) -> str | None:
        if not self.modes.listening_allowed():
            return None  # open workshop / relay cut: no audio processing at all
        if not self.wake.detect(frame):
            return None
        text = self.stt.transcribe(frame)  # one utterance, in memory, never stored
        for word in ("charlie,", "charlie"):
            if text.lower().startswith(word):
                text = text[len(word) :].strip()
                break
        if not self.charlie_available():
            return None
        mode = self.modes.mode()
        trust = Trust.OWNER_KNOWN
        self.audit.append("mic", "mic.command", {"mode": mode.value})
        reply = self.charlie(text, CallContext(trust, mode, "mic"))
        self.tts.synthesize(reply)
        self.replies.append(reply)
        return reply
