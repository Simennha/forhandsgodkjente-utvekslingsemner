"""Home Assistant adapter (REST) with a mock.

Defense in depth for "alarm never": the adapter only knows an allowlist of
(domain, service) pairs. Anything else - alarm_control_panel, switch, script, automation,
button, siren, shell_command, ... - raises before a request is built. The entity id must
also be in the configured entity lists. Combined with a non-admin HA user whose token only
exposes the configured entities, there is no code path that can touch the alarm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from assistants.common.errors import Denied

ALLOWED_SERVICES: frozenset[tuple[str, str]] = frozenset(
    {
        ("light", "turn_on"),
        ("light", "turn_off"),
        ("climate", "set_temperature"),
        ("lock", "lock"),
        ("lock", "unlock"),
    }
)
READABLE_DOMAINS: frozenset[str] = frozenset(
    {"light", "climate", "lock", "sensor", "binary_sensor"}
)


def check_service(
    domain: str, service: str, entity_id: str, allowed_entities: frozenset[str]
) -> None:
    if (domain, service) not in ALLOWED_SERVICES:
        raise Denied(f"home assistant service not allowed: {domain}.{service}")
    if not entity_id.startswith(f"{domain}."):
        raise Denied("entity domain does not match service domain")
    if entity_id not in allowed_entities:
        raise Denied(f"entity not in configured allowlist: {entity_id}")


class HomeAssistant(Protocol):
    def get_state(self, entity_id: str) -> dict[str, Any]: ...
    def call_service(
        self, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> dict[str, Any]: ...


class HomeAssistantREST:
    """HA REST API with a long-lived token of a NON-admin user. NOT verified."""

    def __init__(
        self,
        base_url: str,
        token: str,
        allowed_entities: frozenset[str],
        client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._allowed = allowed_entities
        self._client = client or httpx.Client(
            timeout=10.0, headers={"Authorization": f"Bearer {token}"}
        )

    def get_state(self, entity_id: str) -> dict[str, Any]:
        domain = entity_id.split(".", 1)[0]
        if domain not in READABLE_DOMAINS or entity_id not in self._allowed:
            raise Denied(f"entity not readable: {entity_id}")
        resp = self._client.get(f"{self._base}/api/states/{entity_id}")
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    def call_service(
        self, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        check_service(domain, service, entity_id, self._allowed)
        resp = self._client.post(
            f"{self._base}/api/services/{domain}/{service}", json={"entity_id": entity_id, **data}
        )
        resp.raise_for_status()
        return {"ok": True}


@dataclass
class MockHomeAssistant:
    allowed_entities: frozenset[str]
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, str, str, dict[str, Any]]] = field(default_factory=list)

    def get_state(self, entity_id: str) -> dict[str, Any]:
        domain = entity_id.split(".", 1)[0]
        if domain not in READABLE_DOMAINS or entity_id not in self.allowed_entities:
            raise Denied(f"entity not readable: {entity_id}")
        return self.states.get(entity_id, {"entity_id": entity_id, "state": "unknown"})

    def call_service(
        self, domain: str, service: str, entity_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        check_service(domain, service, entity_id, self.allowed_entities)
        self.calls.append((domain, service, entity_id, dict(data)))
        state = "on" if service == "turn_on" else "off" if service == "turn_off" else service
        self.states[entity_id] = {"entity_id": entity_id, "state": state, **data}
        return {"ok": True}
