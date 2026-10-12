"""Hardware abstraction for the real device: Raspberry Pi Zero 2 W + SIM7600-series HAT.

NOT verified on hardware (see HANDOFF.md). Implements ``KillSwitchHAL`` with:
* gpiozero for the entrance switch (latching, to GND, internal pull-up) and a status LED,
* standard 3GPP AT commands over the HAT's UART for incoming calls (+CLIP caller ID, then
  ATH to hang up - the device never answers), SMS (+CMTI -> AT+CMGR -> AT+CMGD) and
  sending SMS (AT+CMGS).
"""

from __future__ import annotations

import re
import time
from typing import Any

from assistants.killswitch.core import KSState

CLIP = re.compile(r'\+CLIP:\s*"(?P<num>[+0-9]+)"')
CMTI = re.compile(r'\+CMTI:\s*"\w+",(?P<idx>\d+)')
CMGR_HEADER = re.compile(r'\+CMGR:\s*"[^"]*","(?P<num>[+0-9]+)"')


class PiHAL:
    def __init__(self, switch_pin: int = 17, led_pin: int = 27, port: str = "/dev/serial0") -> None:
        import serial  # type: ignore[import-untyped]  # noqa: PLC0415
        from gpiozero import LED, Button  # type: ignore[import-not-found]  # noqa: PLC0415

        self._switch: Any = Button(switch_pin, pull_up=True, bounce_time=0.05)
        self._led: Any = LED(led_pin)
        self._modem: Any = serial.Serial(port, 115200, timeout=0.2)
        for cmd in ("ATE0", "AT+CLIP=1", "AT+CMGF=1", "AT+CNMI=2,1,0,0,0"):
            self._at(cmd)
        self._last_ring: dict[str, float] = {}

    def _at(self, cmd: str, wait: float = 0.3) -> str:
        self._modem.write((cmd + "\r").encode())
        time.sleep(wait)
        return bytes(self._modem.read(4096)).decode(errors="ignore")

    def switch_engaged(self) -> bool:
        return bool(self._switch.is_pressed)

    def poll_modem(self) -> list[tuple[str, str, str]]:
        data = bytes(self._modem.read(4096)).decode(errors="ignore")
        events: list[tuple[str, str, str]] = []
        for m in CLIP.finditer(data):
            number = m.group("num")
            now = time.monotonic()
            # One RING burst = one call: ignore repeated +CLIP lines within 20 s.
            if now - self._last_ring.get(number, 0.0) > 20:
                events.append(("ring", number, ""))
            self._last_ring[number] = now
            self._at("ATH")
        for m in CMTI.finditer(data):
            idx = m.group("idx")
            msg = self._at(f"AT+CMGR={idx}", wait=0.5)
            header = CMGR_HEADER.search(msg)
            body = msg.split("\n", 2)[2].rsplit("OK", 1)[0].strip() if msg.count("\n") >= 2 else ""
            if header:
                events.append(("sms", header.group("num"), body))
            self._at(f"AT+CMGD={idx}")
        return events

    def send_sms(self, to_number: str, body: str) -> bool:
        self._at(f'AT+CMGS="{to_number}"', wait=0.5)
        self._modem.write(body.encode()[:160] + b"\x1a")
        time.sleep(3)
        return "OK" in bytes(self._modem.read(4096)).decode(errors="ignore")

    def set_led(self, state: KSState) -> None:
        if state == KSState.ACTIVE:
            self._led.off()
        elif state == KSState.PAUSED:
            self._led.blink(on_time=0.5, off_time=0.5)
        else:
            self._led.on()
