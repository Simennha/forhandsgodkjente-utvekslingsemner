"""Typed configuration. All limits, windows and model IDs live in config, not in code.

Loaded from YAML (config.yaml, falling back to config.example.yaml). Every model is strict
(``extra="forbid"``) so a typo in a limit fails loudly instead of silently using a default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(os.environ.get("ASSISTANTS_ROOT", Path(__file__).resolve().parents[3]))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Locale(_Strict):
    country: str = "NO"
    language: str = "nb"
    timezone: str = "Europe/Oslo"


class ModelPrice(_Strict):
    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0


class LLMConfig(_Strict):
    router_model: str = "claude-haiku-5-5"
    hard_model: str = "claude-sonnet-5-5"
    lola_model: str = "claude-haiku-5-5"
    extractor_model: str = "claude-haiku-5-5"
    usd_to_nok: float = 10.8
    prices_usd_per_mtok: dict[str, ModelPrice]
    max_tokens: dict[str, int] = Field(
        default_factory=lambda: {"router": 1024, "hard": 4096, "lola": 512, "extractor": 1024}
    )
    backend: Literal["mock", "anthropic"] = "mock"


class Budget(_Strict):
    daily_nok: float
    monthly_nok: float


class OrdersConfig(_Strict):
    level3_single_max_nok: float = 5000.0
    monthly_max_nok: float = 50000.0


class ApprovalsConfig(_Strict):
    passkey_ttl_s: int = 300
    box_ttl_s: int = 600
    approval_box_tools: list[str] = Field(default_factory=list)
    rp_id: str = "charlie.local"
    rp_name: str = "Charlie godkjenning"


class RateLimits(_Strict):
    gateway_per_number_per_hour: int = 30
    gateway_unknown_per_hour: int = 10
    pin_attempts_before_lockout: int = 3
    pin_lockout_s: int = 900
    tool_calls_per_hour: dict[str, int] = Field(default_factory=lambda: {"default": 60})


class IdentityConfig(_Strict):
    owner_numbers: list[str]
    second_alarm_number: str
    lola_number: str
    charlie_number: str
    killswitch_number: str
    known_contacts: list[str] = Field(default_factory=list)


class AnomalyRule(_Strict):
    name: str
    kind: Literal["denied_burst", "level4_attempt", "pin_failures", "cost_spike", "after_hours"]
    threshold: int
    window_s: int
    action: Literal["PAUSED", "STOPPED"] = "PAUSED"


class KillSwitchConfig(_Strict):
    pause_window_s: int = 120
    unknown_rings_to_pause: int = 2
    known_rings_to_pause: int = 1
    stop_window_s: int = 600
    rings_to_stop: int = 3
    physical_switch_action: Literal["PAUSED", "STOPPED"] = "STOPPED"
    lease_ttl_s: int = 30
    max_rings_tracked: int = 256
    anomaly_rules: list[AnomalyRule] = Field(default_factory=list)


class OpenWorkshopConfig(_Strict):
    allow_phone_stt: bool = False


class WorkshopConfig(_Strict):
    wake_words: list[str] = Field(default_factory=lambda: ["charlie"])
    open_workshop: OpenWorkshopConfig = OpenWorkshopConfig()


class TranscriptConfig(_Strict):
    raw_retention_days: int = 7
    consent_message: str = (
        "Hei. Denne samtalen blir transkribert automatisk. Rå tekst slettes etter sju dager. "
        "Trykk 1 eller si ja for å fortsette, trykk 2 for å avslutte."
    )


class HeartbeatConfig(_Strict):
    stale_after_s: int = 120


class HouseConfig(_Strict):
    lights: list[str] = Field(default_factory=list)
    climate: list[str] = Field(default_factory=list)
    locks: list[str] = Field(default_factory=list)
    workshop_lights: list[str] = Field(default_factory=list)
    workshop_sensors: list[str] = Field(default_factory=list)
    min_temp_c: float = 5.0
    max_temp_c: float = 25.0


class ActionTokenConfig(_Strict):
    ttl_s: int = 60


class AppConfig(_Strict):
    locale: Locale = Locale()
    llm: LLMConfig
    budgets: dict[str, Budget]
    orders: OrdersConfig = OrdersConfig()
    approvals: ApprovalsConfig = ApprovalsConfig()
    rate_limits: RateLimits = RateLimits()
    identity: IdentityConfig
    killswitch: KillSwitchConfig = KillSwitchConfig()
    workshop: WorkshopConfig = WorkshopConfig()
    transcripts: TranscriptConfig = TranscriptConfig()
    heartbeat: HeartbeatConfig = HeartbeatConfig()
    house: HouseConfig = HouseConfig()
    action_tokens: ActionTokenConfig = ActionTokenConfig()


def config_path() -> Path:
    env = os.environ.get("ASSISTANTS_CONFIG")
    if env:
        return Path(env)
    local = ROOT / "config.yaml"
    return local if local.exists() else ROOT / "config.example.yaml"


def load_config(path: Path | None = None) -> AppConfig:
    target = path or config_path()
    with target.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return AppConfig.model_validate(raw)
