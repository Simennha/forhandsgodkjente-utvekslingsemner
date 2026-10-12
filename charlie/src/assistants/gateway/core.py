"""Gateway core: transport-agnostic handling of every inbound channel.

Responsibilities (all in code, no LLM):
* rate limit per number/session,
* identity (known number, PIN via DTMF, duress code, test word),
* workshop mode (normal / guest / open workshop) - guest automatically for unknown devices,
* consent before any transcription, and the consent log,
* turning everything into an Event with source, time and trust,
* routing: Charlie's line -> Charlie, Lola's line / door phone / guest chat -> Lola ONLY.

HTTP specifics (Twilio webhooks, signatures) live in assistants.services.gateway_http.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from assistants.adapters.speech import SpeechToText, TextToSpeech
from assistants.audit import AuditLog
from assistants.common.clock import Clock
from assistants.common.config import AppConfig
from assistants.events import Event, EventQueue, Mode, Source, Trust
from assistants.executor.core import CallContext
from assistants.identity import LIVENESS_REPLY, LOCKED_REPLY, IdentityService, Session
from assistants.lola_queue.store import Channel
from assistants.storage.transcripts import ConsentRecord, TranscriptStore

Line = Literal["charlie", "lola"]
CharlieHandler = Callable[[str, CallContext], str]
LolaHandler = Callable[[str, Channel, str | None], str]

RATE_LIMITED = "For mange henvendelser akkurat nå. Prøv igjen senere."
NO_CONSENT = "Greit, da avslutter vi. Du kan sende SMS i stedet."
OPEN_WORKSHOP_PHONE = "Verkstedet er i åpen modus, og jeg lytter ikke nå. Send gjerne en SMS."
UNKNOWN_ON_CHARLIE = "Dette nummeret tar ikke imot henvendelser. For verkstedet, ring resepsjonen."
CHARLIE_PAUSED = "Charlie er ikke tilgjengelig akkurat nå."
LOLA_GREETING = "Hei, dette er resepsjonen. Hva kan jeg hjelpe med?"


def number_ref(number: str) -> str:
    """Short, non-reversible reference for logs (numbers themselves stay in the consent log)."""
    return hashlib.sha256(number.encode()).hexdigest()[:12]


def log_trust(trust: Trust) -> str:
    # The duress state is never visible in logs: it is logged exactly like a PIN session.
    return Trust.OWNER_PIN.value if trust == Trust.DURESS else trust.value


class RateLimiter:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._hits: dict[str, deque[datetime]] = {}

    MAX_KEYS = 10_000

    def allow(self, key: str, limit: int, window_s: int = 3600) -> bool:
        now = self._clock.now()
        if len(self._hits) > self.MAX_KEYS:  # bounded memory under number-spoofing floods
            cutoff = now - timedelta(seconds=window_s)
            self._hits = {k: q for k, q in self._hits.items() if q and q[-1] > cutoff}
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > timedelta(seconds=window_s):
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


@dataclass
class CallState:
    call_id: str
    line: Line
    session: Session
    consent: Literal["pending", "accepted", "declined"] = "pending"
    lines: list[str] = field(default_factory=list)


class Gateway:
    def __init__(
        self,
        cfg: AppConfig,
        identity: IdentityService,
        queue: EventQueue,
        audit: AuditLog,
        clock: Clock,
        stt: SpeechToText,
        tts: TextToSpeech,
        transcripts: TranscriptStore,
        workshop_mode: Callable[[], Mode],
        charlie: CharlieHandler,
        lola: LolaHandler,
        charlie_available: Callable[[], bool],
    ) -> None:
        self._cfg = cfg
        self._identity = identity
        self._queue = queue
        self._audit = audit
        self._clock = clock
        self._stt = stt
        self._tts = tts
        self._transcripts = transcripts
        self._mode = workshop_mode
        self._charlie = charlie
        self._lola = lola
        self._charlie_available = charlie_available
        self._rate = RateLimiter(clock)
        self.calls: dict[str, CallState] = {}
        self.spoken: list[bytes] = []

    # ------------------------------------------------------------ helpers
    def _rate_ok(self, number: str, owner: bool) -> bool:
        limits = self._cfg.rate_limits
        limit = limits.gateway_per_number_per_hour if owner else limits.gateway_unknown_per_hour
        ok = self._rate.allow(f"n:{number}", limit)
        if not ok:
            self._audit.append("gateway", "rate.limited", {"number": number_ref(number)})
        return ok

    def _event(
        self,
        source: Source,
        trust: Trust,
        agent: Literal["charlie", "lola"],
        mode: Mode,
        session_id: str | None,
        payload: dict[str, object],
    ) -> Event:
        ev = Event(
            ts=self._clock.now(),
            source=source,
            trust=trust,
            agent=agent,
            mode=mode,
            session_id=session_id,
            payload=payload,
        )
        self._queue.put(ev)
        return ev

    def _say(self, text: str) -> str:
        self.spoken.append(self._tts.synthesize(text))
        return text

    # ------------------------------------------------------------ voice
    def call_started(self, line: Line, call_id: str, from_number: str) -> str:
        session = self._identity.start_session(line, from_number, self._mode())
        self._audit.append(
            "gateway",
            "call.started",
            {
                "line": line,
                "call": call_id,
                "number": number_ref(from_number),
                "trust": log_trust(session.trust),
                "mode": session.mode.value,
            },
        )
        if not self._rate_ok(from_number, session.owner):
            return self._say(RATE_LIMITED)
        if line == "charlie" and not session.owner:
            # Unknown number on Charlie's line: guest treatment, nothing is opened or recorded.
            # The call is not registered, so no later turn can reach STT or Charlie.
            return self._say(UNKNOWN_ON_CHARLIE)
        self.calls[call_id] = CallState(call_id, line, session)
        # Always play the transcription notice before anything is recorded.
        return self._say(self._cfg.transcripts.consent_message)

    def consent(self, call_id: str, accepted: bool) -> str:
        state = self.calls[call_id]
        state.consent = "accepted" if accepted else "declined"
        self._transcripts.log_consent(
            ConsentRecord(
                self._clock.now(),
                call_id,
                state.line,
                state.session.number,
                "accepted" if accepted else "declined",
            )
        )
        self._audit.append("gateway", "consent", {"call": call_id, "answer": state.consent})
        if not accepted:
            return self._say(NO_CONSENT)
        return self._say("Takk. Hva kan jeg hjelpe med?")

    def dtmf(self, call_id: str, digits: str) -> str:
        state = self.calls[call_id]
        if state.line != "charlie":
            return self._say("Tastetrykk brukes ikke her.")
        session, reply = self._identity.submit_code(state.session, digits)
        state.session = session
        verified = session.trust in (Trust.OWNER_PIN, Trust.DURESS)
        # Identical audit entry for PIN and duress. The digits never leave this function.
        # Attempts during a lockout are not checked and not counted as failures (red team #4).
        kind = "auth.pin_verified" if verified else "auth.pin_failed"
        if reply == LOCKED_REPLY:
            kind = "auth.locked"
        self._audit.append("gateway", kind, {"call": call_id, "number": number_ref(session.number)})
        return self._say(reply)

    def audio(self, call_id: str, audio: bytes) -> str:
        state = self.calls[call_id]
        session = state.session
        if not self._rate_ok(session.number, session.owner):
            return self._say(RATE_LIMITED)
        if state.line == "charlie" and not session.owner:
            return self._say(UNKNOWN_ON_CHARLIE)
        if state.consent != "accepted":
            return self._say(self._cfg.transcripts.consent_message)
        if (
            self._mode() == Mode.OPEN_WORKSHOP
            and not self._cfg.workshop.open_workshop.allow_phone_stt
        ):
            return self._say(OPEN_WORKSHOP_PHONE)
        text = self._stt.transcribe(audio)
        if self._identity.is_test_word(text):
            # Never stored, never forwarded to any model (red team #7).
            self._audit.append("gateway", "liveness", {"ref": call_id})
            return self._say(LIVENESS_REPLY if state.line == "charlie" else LOLA_GREETING)
        state.lines.append(text)
        if state.line == "lola":
            self._event(
                Source.PHONE_LOLA,
                Trust.GUEST,
                "lola",
                Mode.GUEST,
                session.session_id,
                {"call": call_id},
            )
            return self._say(self._lola(text, "phone_lola", session.number))
        return self._say(self._charlie_turn(text, session, Source.PHONE_CHARLIE, "phone", call_id))

    def call_ended(self, call_id: str) -> None:
        state = self.calls.pop(call_id, None)
        if state is None:
            return
        if state.consent == "accepted" and state.lines:
            self._transcripts.add_raw(
                call_id,
                state.line,
                "\n".join(state.lines),
                self._clock.now(),
                self._cfg.transcripts.raw_retention_days,
            )
        self._audit.append("gateway", "call.ended", {"call": call_id, "turns": len(state.lines)})

    # ------------------------------------------------------------ Charlie turn
    def _charlie_turn(
        self, text: str, session: Session, source: Source, channel: str, ref: str
    ) -> str:
        if self._identity.is_test_word(text):
            self._audit.append("gateway", "liveness", {"ref": ref})
            return LIVENESS_REPLY
        if not self._charlie_available():
            return CHARLIE_PAUSED
        mode = session.mode
        if self._mode() == Mode.OPEN_WORKSHOP:
            mode = Mode.OPEN_WORKSHOP
        self._event(source, session.trust, "charlie", mode, session.session_id, {"ref": ref})
        ctx = CallContext(session.trust, mode, channel, session.session_id)
        return self._charlie(text, ctx)

    # ------------------------------------------------------------ SMS / chat / door
    def sms(self, line: Line, from_number: str, body: str) -> str:
        session = self._identity.start_session(line, from_number, self._mode())
        if not self._rate_ok(from_number, session.owner):
            return RATE_LIMITED
        self._audit.append(
            "gateway",
            "sms.in",
            {"line": line, "number": number_ref(from_number), "trust": log_trust(session.trust)},
        )
        if self._identity.is_test_word(body):
            return LIVENESS_REPLY if line == "charlie" and session.owner else LOLA_GREETING
        if line == "lola":
            self._event(Source.SMS_LOLA, Trust.GUEST, "lola", Mode.GUEST, None, {})
            return self._lola(body, "sms_lola", from_number)
        if not session.owner:
            return UNKNOWN_ON_CHARLIE
        # SMS never carries a PIN: at most OWNER_KNOWN (levels 1-2).
        return self._charlie_turn(body, session, Source.SMS_CHARLIE, "sms", number_ref(from_number))

    def chat(self, passkey_session: bool, text: str, session_id: str) -> str:
        """Owner chat in the PWA. A passkey-authenticated session counts as number+PIN.
        The PWA never asks for the PIN in plain text."""
        if not self._rate.allow(
            f"chat:{session_id}", self._cfg.rate_limits.gateway_per_number_per_hour
        ):
            return RATE_LIMITED
        trust = Trust.OWNER_PIN if passkey_session else Trust.OWNER_KNOWN
        session = Session(
            session_id, "charlie", "chat", True, trust, self._mode(), self._clock.now()
        )
        if self._identity.is_test_word(text):
            return LIVENESS_REPLY
        return self._charlie_turn(text, session, Source.CHAT, "chat", session_id)

    def guest_chat(self, text: str, session_id: str, client: str = "local") -> str:
        """Public guest chat. Limited per client address and globally, not by the session id
        the caller chooses (red team #11)."""
        limits = self._cfg.rate_limits
        if not (
            self._rate.allow(f"gchat-ip:{client}", limits.gateway_unknown_per_hour)
            and self._rate.allow("gchat-all", limits.gateway_unknown_per_hour * 10)
        ):
            return RATE_LIMITED
        if self._identity.is_test_word(text):
            return LOLA_GREETING
        self._event(Source.CHAT_GUEST, Trust.GUEST, "lola", Mode.GUEST, session_id, {})
        return self._lola(text, "chat_guest", None)

    def door(self, audio: bytes) -> str:
        """Door phone: ALWAYS Lola, never Charlie. Speech here is a visitor speaking."""
        if not self._rate.allow("door", self._cfg.rate_limits.gateway_unknown_per_hour * 3):
            return RATE_LIMITED
        if (
            self._mode() == Mode.OPEN_WORKSHOP
            and not self._cfg.workshop.open_workshop.allow_phone_stt
        ):
            return self._say(OPEN_WORKSHOP_PHONE)
        text = self._stt.transcribe(audio)
        if self._identity.is_test_word(text):
            return self._say(LOLA_GREETING)
        self._event(Source.DOOR, Trust.GUEST, "lola", Mode.GUEST, None, {})
        return self._say(self._lola(text, "door", None))
