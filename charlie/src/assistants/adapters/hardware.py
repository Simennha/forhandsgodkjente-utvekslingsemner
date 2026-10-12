"""Hardware abstraction for the workshop: microphone relay and camera audio.

Open-workshop mode physically disconnects the microphones with a relay. The relay is wired
normally-open, so loss of power or a crashed process also means "microphones cut".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class MicRelay(Protocol):
    def set_connected(self, connected: bool) -> None: ...
    def is_connected(self) -> bool: ...


class GpioMicRelay:
    """Relay on a GPIO pin (gpiozero). NOT verified on hardware."""

    def __init__(self, pin: int) -> None:
        from gpiozero import OutputDevice  # type: ignore[import-not-found]  # noqa: PLC0415

        # active_high + initial_value False => relay de-energised (mics cut) at start.
        self._dev: Any = OutputDevice(pin, active_high=True, initial_value=False)

    def set_connected(self, connected: bool) -> None:
        if connected:
            self._dev.on()
        else:
            self._dev.off()

    def is_connected(self) -> bool:
        return bool(self._dev.value)


@dataclass
class SimMicRelay:
    connected: bool = False
    history: list[bool] = field(default_factory=list)

    def set_connected(self, connected: bool) -> None:
        self.connected = connected
        self.history.append(connected)

    def is_connected(self) -> bool:
        return self.connected


class CameraAudio(Protocol):
    def set_audio_enabled(self, enabled: bool) -> None: ...
    def audio_enabled(self) -> bool: ...


@dataclass
class SimCameraAudio:
    enabled: bool = False

    def set_audio_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def audio_enabled(self) -> bool:
        return self.enabled
