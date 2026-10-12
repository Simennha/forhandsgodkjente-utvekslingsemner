"""Workshop modes: Normal, Guest, Open workshop.

Open workshop: microphones are cut PHYSICALLY by a relay (normally open: no power = cut),
camera audio is turned off, nothing listens and nothing is transcribed. Charlie works only in
the background (inventory, orders, sensors - see rules.engine.OPEN_WORKSHOP_TOOLS).
The mode is set by the owner (PWA / wall switch), never by Charlie: there is no tool for it.
"""

from __future__ import annotations

from collections.abc import Callable

from assistants.adapters.hardware import CameraAudio, MicRelay
from assistants.audit import AuditLog
from assistants.events import Mode


class WorkshopModeController:
    def __init__(
        self,
        relay: MicRelay,
        camera_audio: CameraAudio,
        audit: AuditLog,
        on_change: Callable[[Mode], None] | None = None,
    ) -> None:
        self._relay = relay
        self._camera = camera_audio
        self._audit = audit
        self._on_change = on_change or (lambda _m: None)
        self._mode = Mode.NORMAL
        self._apply(Mode.NORMAL)

    def _apply(self, mode: Mode) -> None:
        listening = mode != Mode.OPEN_WORKSHOP
        # Cut first, then record: if anything below fails, the microphones stay cut.
        if not listening:
            self._relay.set_connected(False)
            self._camera.set_audio_enabled(False)
        else:
            self._relay.set_connected(True)
            self._camera.set_audio_enabled(False)  # cameras never record sound

    def set_mode(self, mode: Mode, actor: str = "owner") -> None:
        self._apply(mode)
        self._mode = mode
        self._audit.append(actor, "workshop.mode", {"mode": mode.value})
        self._on_change(mode)

    def mode(self) -> Mode:
        return self._mode

    def listening_allowed(self) -> bool:
        return self._mode != Mode.OPEN_WORKSHOP and self._relay.is_connected()
