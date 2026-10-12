"""Choose real adapters when a real credential is configured, otherwise the simulator.

Every fallback to a simulator is logged at WARNING and listed by ``simulated_providers``,
so it is always visible which parts are not talking to the real world.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from assistants.adapters.calendar import CalendarProvider, GoogleCalendar, MockCalendar
from assistants.adapters.email import EmailProvider, GmailEmail, MockEmail
from assistants.adapters.homeassistant import HomeAssistant, HomeAssistantREST, MockHomeAssistant
from assistants.adapters.llm import AnthropicLLM, LLMClient
from assistants.adapters.notifier import Notifier, SimNotifier
from assistants.adapters.secrets import SecretStore
from assistants.adapters.supplier import MockSupplierOrders, SupplierOrders
from assistants.adapters.telephony import SimTelephony, TelephonyProvider, TwilioTelephony
from assistants.agents.mock_brains import mock_llm
from assistants.common.config import AppConfig
from assistants.services.common import is_fake, log


@dataclass
class Providers:
    calendar: CalendarProvider
    email: EmailProvider
    ha: HomeAssistant
    notifier: Notifier
    telephony: TelephonyProvider
    supplier: SupplierOrders
    simulated: list[str] = field(default_factory=list)


def _secret(store: SecretStore, name: str) -> str | None:
    if not store.has(name):
        return None
    value = store.get(name)
    return None if is_fake(value) else value


def providers(cfg: AppConfig, store: SecretStore) -> Providers:
    simulated: list[str] = []
    google = _secret(store, "google.access_token")
    calendar: CalendarProvider
    email: EmailProvider
    if google:
        calendar, email = GoogleCalendar(google), GmailEmail(google)
    else:
        calendar, email = MockCalendar(), MockEmail()
        simulated += ["google-calendar", "gmail"]
    allowed = frozenset(
        cfg.house.lights
        + cfg.house.climate
        + cfg.house.locks
        + cfg.house.workshop_lights
        + cfg.house.workshop_sensors
    )
    ha_token = _secret(store, "homeassistant.token")
    ha: HomeAssistant
    if ha_token:
        ha = HomeAssistantREST(store.get("homeassistant.url"), ha_token, allowed)
    else:
        ha = MockHomeAssistant(allowed_entities=allowed)
        simulated.append("home-assistant")
    sid = _secret(store, "twilio.account_sid")
    telephony: TelephonyProvider
    if sid:
        telephony = TwilioTelephony(sid, store.get("twilio.auth_token"))
    else:
        telephony = SimTelephony(signing_key=store.get("twilio.auth_token"))
        simulated.append("twilio")
    simulated += ["ntfy", "supplier-orders"]
    for name in simulated:
        log.warning("SIMULATED provider in use: %s (not verified against the real service)", name)
    return Providers(calendar, email, ha, SimNotifier(), telephony, MockSupplierOrders(), simulated)


def llm_backend(cfg: AppConfig, store: SecretStore, key_name: str) -> LLMClient:
    key = _secret(store, key_name) or os.environ.get("ANTHROPIC_API_KEY")
    if cfg.llm.backend == "anthropic" and key and not is_fake(key):
        return AnthropicLLM(key)
    log.warning("SIMULATED LLM in use (deterministic mock), backend=%s", cfg.llm.backend)
    return mock_llm(compromised=os.environ.get("MOCK_LLM_COMPROMISED") == "1")
