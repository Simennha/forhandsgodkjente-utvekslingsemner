"""Identity: known numbers, PIN, duress code and test word. Runs in the gateway.

* Known number alone           -> Trust.OWNER_KNOWN (levels 1-2).
* Known number + correct PIN   -> Trust.OWNER_PIN   (level 3 possible, still confirmed).
* Known number + duress code   -> Trust.DURESS. The caller hears EXACTLY the same reply as
  for a correct PIN. Only duress-safe level 1 tools work, and the kill-switch device sends
  a silent alarm on its own channel.
* Test word (any number)       -> a liveness reply; no level is opened.

The PIN, duress code and test word are only ever compared as salted+peppered scrypt hashes
and never stored, logged or passed on. Only the boolean outcome continues, and the duress
outcome is not distinguishable in any log or reply (see docs/threat-model.md).
Unknown numbers on Charlie's line get guest treatment: no tools at all.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from assistants.adapters.secrets import SecretStore
from assistants.common.clock import Clock
from assistants.common.config import IdentityConfig, RateLimits
from assistants.common.crypto import credential_matches
from assistants.events import Mode, Trust

PIN_ACCEPTED_REPLY = "Takk, PIN godkjent."
PIN_REJECTED_REPLY = "Det stemte ikke."
LOCKED_REPLY = "For mange forsøk. Prøv igjen senere."
LIVENESS_REPLY = "Charlie er i drift."


@dataclass(frozen=True)
class Credential:
    salt: bytes
    hash: bytes

    @classmethod
    def from_json(cls, text: str) -> Credential:
        data = json.loads(text)
        return cls(bytes.fromhex(data["salt"]), bytes.fromhex(data["hash"]))


@dataclass(frozen=True)
class Credentials:
    pepper: bytes
    pin: Credential
    duress: Credential
    test_word: Credential

    @classmethod
    def from_store(cls, store: SecretStore) -> Credentials:
        return cls(
            pepper=bytes.fromhex(store.get("identity.pepper")),
            pin=Credential.from_json(store.get("identity.pin")),
            duress=Credential.from_json(store.get("identity.duress")),
            test_word=Credential.from_json(store.get("identity.test_word")),
        )


@dataclass(frozen=True)
class Session:
    session_id: str
    agent: str
    number: str
    owner: bool
    trust: Trust
    mode: Mode
    created_at: datetime

    @property
    def duress(self) -> bool:
        return self.trust == Trust.DURESS


@dataclass
class _Attempts:
    failures: list[datetime] = field(default_factory=list)
    locked_until: datetime | None = None


class IdentityService:
    def __init__(
        self,
        cfg: IdentityConfig,
        limits: RateLimits,
        creds: Credentials,
        clock: Clock,
        on_duress: Callable[[], None],
        on_pin_failure: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._limits = limits
        self._creds = creds
        self._clock = clock
        self._on_duress = on_duress
        self._on_pin_failure = on_pin_failure or (lambda _n: None)
        self._attempts: dict[str, _Attempts] = {}
        self._owner_numbers = frozenset(cfg.owner_numbers)

    def is_owner_number(self, number: str) -> bool:
        return number in self._owner_numbers

    def start_session(self, agent: str, number: str, workshop_mode: Mode) -> Session:
        owner = agent == "charlie" and self.is_owner_number(number)
        trust = Trust.GUEST if agent == "lola" else Trust.OWNER_KNOWN if owner else Trust.UNKNOWN
        # Unknown device/number => guest mode for this session, automatically.
        mode = workshop_mode if owner else Mode.GUEST
        if workshop_mode == Mode.OPEN_WORKSHOP:
            mode = Mode.OPEN_WORKSHOP
        return Session(
            session_id=secrets.token_urlsafe(16),
            agent=agent,
            number=number,
            owner=owner,
            trust=trust,
            mode=mode,
            created_at=self._clock.now(),
        )

    def _locked(self, number: str) -> bool:
        state = self._attempts.get(number)
        return bool(state and state.locked_until and self._clock.now() < state.locked_until)

    def submit_code(self, session: Session, digits: str) -> tuple[Session, str]:
        """Handle DTMF digits on a call. Returns the (possibly upgraded) session and a reply.

        Correct PIN and duress code produce identical replies and identical-looking sessions.
        """
        if not session.owner or session.agent != "charlie":
            return session, PIN_REJECTED_REPLY
        if self._locked(session.number):
            return session, LOCKED_REPLY
        c = self._creds
        # Always compute both hashes so PIN and duress take the same time.
        pin_ok = credential_matches(digits, c.pin.salt, c.pin.hash, c.pepper)
        duress_ok = credential_matches(digits, c.duress.salt, c.duress.hash, c.pepper)
        if pin_ok:
            self._attempts.pop(session.number, None)
            return replace(session, trust=Trust.OWNER_PIN), PIN_ACCEPTED_REPLY
        if duress_ok:
            self._attempts.pop(session.number, None)
            self._on_duress()
            return replace(session, trust=Trust.DURESS), PIN_ACCEPTED_REPLY
        self._record_failure(session.number)
        return session, PIN_REJECTED_REPLY

    def _record_failure(self, number: str) -> None:
        now = self._clock.now()
        state = self._attempts.setdefault(number, _Attempts())
        window = timedelta(seconds=self._limits.pin_lockout_s)
        state.failures = [t for t in state.failures if now - t < window] + [now]
        if len(state.failures) >= self._limits.pin_attempts_before_lockout:
            state.locked_until = now + window
            state.failures = []
        self._on_pin_failure(number)

    def is_test_word(self, utterance: str) -> bool:
        c = self._creds
        return credential_matches(utterance, c.test_word.salt, c.test_word.hash, c.pepper)
