"""Workshop modes: Normal, Guest, Open workshop.

Open workshop: microphones are cut PHYSICALLY by a relay (normally open: no power = cut),
camera audio is turned off, nothing listens and nothing is transcribed. Charlie works only in
the background (inventory, orders, sensors - see rules.engine.OPEN_WORKSHOP_TOOLS).
The mode is set by the owner (PWA / wall switch), never by Charlie: there is no tool for it.

The mode is saved durably. After a restart the saved mode is restored; if nothing (or
something unreadable) is saved, the controller starts in OPEN_WORKSHOP - microphones cut -
until the owner sets a mode. A crash can therefore never turn the microphones back on.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from assistants.adapters.hardware import CameraAudio, MicRelay
from assistants.audit import AuditLog
from assistants.events import Mode


class ModeStore(Protocol):
    def load(self) -> Mode | None: ...
    def save(self, mode: Mode) -> None: ...


@dataclass
class MemoryModeStore:
    mode: Mode | None = None

    def load(self) -> Mode | None:
        return self.mode

    def save(self, mode: Mode) -> None:
        self.mode = mode


class FileModeStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> Mode | None:
        try:
            return Mode(json.loads(self._path.read_text(encoding="utf-8"))["mode"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, mode: Mode) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"mode": mode.value}), encoding="utf-8")
        os.replace(tmp, self._path)


class WorkshopModeController:
    def __init__(
        self,
        relay: MicRelay,
        camera_audio: CameraAudio,
        audit: AuditLog,
        on_change: Callable[[Mode], None] | None = None,
        store: ModeStore | None = None,
    ) -> None:
        self._relay = relay
        self._camera = camera_audio
        self._audit = audit
        self._on_change = on_change or (lambda _m: None)
        self._store = store or MemoryModeStore()
        saved = self._store.load()
        # Unknown after a restart => fail safe: microphones stay cut.
        self._mode = saved if saved is not None else Mode.OPEN_WORKSHOP
        self._apply(self._mode)

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
        if mode == Mode.OPEN_WORKSHOP:
            self._apply(mode)
            self._store.save(mode)
        else:
            self._store.save(mode)  # persist before reconnecting microphones
            self._apply(mode)
        self._mode = mode
        self._audit.append(actor, "workshop.mode", {"mode": mode.value})
        self._on_change(mode)

    def mode(self) -> Mode:
        return self._mode

    def listening_allowed(self) -> bool:
        return self._mode != Mode.OPEN_WORKSHOP and self._relay.is_connected()
