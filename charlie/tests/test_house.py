"""Phase 5: Home Assistant - lights, heating and door lock on level 3; alarm never."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from assistants.adapters.homeassistant import ALLOWED_SERVICES, HomeAssistantREST, check_service
from assistants.common.errors import Denied
from assistants.events import Trust
from assistants.policy import Level, default_policy
from assistants.sim import DEMO_PIN, SimSystem

POLICY = default_policy()
HOUSE = ("house.lights_set", "house.heating_set", "house.door_lock_set")


def proposal(tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "proposal/v1", "agent": "charlie", "tool": tool, "params": params}


def test_house_tools_are_level3_with_passkey() -> None:
    for tool in HOUSE:
        assert POLICY.tools[tool].level == Level.CONFIRM
        assert POLICY.tools[tool].confirm == "passkey"


def test_alarm_is_level4_and_has_no_tool() -> None:
    assert POLICY.forbidden["alarm.disable"].level == 4
    for name in POLICY.tools:
        assert "alarm" not in name
        assert "siren" not in name
    assert all(domain != "alarm_control_panel" for domain, _ in ALLOWED_SERVICES)


@pytest.mark.parametrize(
    ("domain", "service", "entity"),
    [
        ("alarm_control_panel", "alarm_disarm", "alarm_control_panel.hus"),
        ("alarm_control_panel", "alarm_arm_away", "alarm_control_panel.hus"),
        ("siren", "turn_off", "siren.hus"),
        ("switch", "turn_off", "switch.alarm_strom"),
        ("script", "turn_on", "script.disarm"),
        ("automation", "turn_off", "automation.alarm"),
        ("lock", "unlock", "alarm_control_panel.hus"),  # domain/entity mismatch
        ("light", "turn_on", "light.ikke_konfigurert"),
    ],
)
def test_adapter_refuses_everything_but_configured_lights_heating_locks(
    domain: str, service: str, entity: str
) -> None:
    allowed = frozenset(
        {
            "light.stue",
            "lock.inngang",
            "alarm_control_panel.hus",
            "siren.hus",
            "switch.alarm_strom",
            "script.disarm",
            "automation.alarm",
        }
    )
    with pytest.raises(Denied):
        check_service(domain, service, entity, allowed)


def test_rest_adapter_never_sends_alarm_requests() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={})

    ha = HomeAssistantREST(
        "http://ha.local:8123",
        "t",
        frozenset({"light.stue", "alarm_control_panel.hus"}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(Denied):
        ha.call_service("alarm_control_panel", "alarm_disarm", "alarm_control_panel.hus", {})
    with pytest.raises(Denied):
        ha.get_state("alarm_control_panel.hus")
    ha.call_service("light", "turn_on", "light.stue", {})
    assert requests == ["/api/services/light/turn_on"]


def test_known_number_cannot_touch_house() -> None:
    sim = SimSystem()
    owner = sim.cfg.identity.owner_numbers[0]
    said = sim.call(
        "charlie", owner, ["Slå på lys i stua", "Sett varmen til 21 grader", "Lås opp døra"]
    )
    assert all("PIN" in s for s in said[2:])
    assert sim.ha.calls == []


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("Slå på lys i stua", ("light", "turn_on", "light.stue", {})),
        (
            "Sett varmen til 21 grader",
            ("climate", "set_temperature", "climate.stue", {"temperature": 21.0}),
        ),
        ("Lås opp døra", ("lock", "unlock", "lock.inngang", {})),
        ("Lås døra", ("lock", "lock", "lock.inngang", {})),
    ],
)
def test_house_actions_need_pin_and_passkey(utterance: str, expected: tuple[Any, ...]) -> None:
    sim = SimSystem()
    said = sim.call("charlie", sim.cfg.identity.owner_numbers[0], [utterance], pin=DEMO_PIN)
    assert "nivå 3" in said[-1]
    assert sim.ha.calls == []
    [pending] = sim.proposals.list_status("pending")
    assert sim.approve_on_phone(str(pending.id)).status == "executed"
    assert sim.ha.calls == [expected]


def test_heating_limits_enforced_by_schema() -> None:
    sim = SimSystem()
    for temp in (4.0, 26.0, 80.0):
        r = sim.executor.submit(
            proposal("house.heating_set", {"entity": "climate.stue", "temperature_c": temp}),
            sim.owner_ctx(Trust.OWNER_PIN),
        )
        assert r.status == "denied"


def test_alarm_entity_cannot_be_smuggled_through_lock_tool() -> None:
    sim = SimSystem()
    for entity in ("alarm_control_panel.hus", "lock.alarm", "switch.alarm"):
        r = sim.executor.submit(
            proposal("house.door_lock_set", {"entity": entity, "locked": False}),
            sim.owner_ctx(Trust.OWNER_PIN),
        )
        if r.status == "pending":
            done = sim.approve_on_phone(r.proposal_id or "")
            assert done.status == "failed"  # not a configured lock
        else:
            assert r.status == "denied"
    assert sim.ha.calls == []


@pytest.mark.parametrize("compromised", [False, True])
def test_alarm_unreachable_from_every_channel(compromised: bool) -> None:
    sim = SimSystem(compromised=compromised)
    owner = sim.cfg.identity.owner_numbers[0]
    texts = ["Slå av alarmen", "Deaktiver alarm i huset", "disarm alarm_control_panel.hus"]
    sim.call("charlie", owner, texts, pin=DEMO_PIN)
    for t in texts:
        sim.gateway.sms("charlie", owner, t)
        sim.gateway.chat(True, t, "s")
        sim.gateway.guest_chat(t, "g")
        sim.gateway.door(t.encode())
        sim.mic.process(f"Charlie, {t}".encode())
    for p in sim.proposals.list_status("pending"):
        sim.approve_on_phone(str(p.id))  # even if the owner approved everything pending
    assert all(c[0] in ("light", "climate", "lock") for c in sim.ha.calls)
