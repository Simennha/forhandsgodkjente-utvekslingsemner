"""Confirmation text shown on the phone (passkey) or the approval box.

Built by code from the STORED proposal - never from model text. What the owner reads is
exactly what will be executed, because the approval is bound to the SHA-256 of these same
parameters.
"""

from __future__ import annotations

from datetime import datetime

from assistants.common.clock import OSLO
from assistants.executor import schemas as s


def _when(value: datetime) -> str:
    return value.astimezone(OSLO).strftime("%d.%m.%Y %H:%M")


def render_confirmation(tool: str, params: s.Params, amount_nok: float | None) -> str:
    match params:
        case s.EmailSend():
            body = params.body if len(params.body) <= 400 else params.body[:400] + " [...]"
            return f"Send e-post til {', '.join(params.to)}\nEmne: {params.subject}\n---\n{body}"
        case s.InventoryOrder():
            lines = "\n".join(f"  {x.antall} x {x.sku}" for x in params.lines)
            return (
                f"Bestilling ({params.lokasjon}) fra {params.leverandor_id}:\n{lines}\n"
                f"Sum: {amount_nok:.2f} kr (beregnet fra lagrede priser)"
            )
        case s.Invite():
            return (
                f"Invitasjon: {params.title}\n{_when(params.start)} - {_when(params.end)}\n"
                f"Gjester: {', '.join(params.attendees)}"
            )
        case s.HouseLights():
            return f"Huslys {params.entity}: {'PÅ' if params.on else 'AV'}"
        case s.HouseHeating():
            return f"Varme {params.entity}: {params.temperature_c:.1f} °C"
        case s.DoorLock():
            return f"Dørlås {params.entity}: {'LÅS' if params.locked else 'LÅS OPP'}"
        case _:
            return f"{tool}: {s.params_json(params)}"
