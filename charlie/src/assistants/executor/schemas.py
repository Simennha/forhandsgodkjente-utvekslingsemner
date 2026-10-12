"""Fixed JSON format for proposals, validated against a strict schema per tool.

The executor accepts NOTHING else: no free text, no extra keys, no unknown tools.
Each tool's parameters are a Pydantic model with ``extra="forbid"``, strict types and
length limits. tests/test_tool_registry.py proves that this registry and
policy/levels.yaml contain exactly the same tools.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from assistants.common.errors import ValidationFailed

Short = Annotated[str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)]
Text = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
Entity = Annotated[str, StringConstraints(pattern=r"^[a-z_]+\.[a-z0-9_]+$", max_length=80)]
Location = Literal["NO", "CH"]
EmailAddr = Annotated[
    str,
    StringConstraints(
        pattern=r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,24}$",
        max_length=254,
    ),
]
_SKU = re.compile(r"^[A-Z0-9][A-Z0-9\-]{1,39}$")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must include a timezone")
    return value


Aware = Annotated[datetime, AfterValidator(_aware)]


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class NoParams(Params):
    pass


class TimeRange(Params):
    start: Aware
    end: Aware

    @model_validator(mode="after")
    def _order(self) -> TimeRange:
        if not self.start < self.end <= self.start + timedelta(days=62):
            raise ValueError("end must be after start and within 62 days")
        return self


class InventoryRead(Params):
    lokasjon: Location | None = None
    sku: Annotated[str, StringConstraints(max_length=40)] | None = None
    sok: Annotated[str, StringConstraints(max_length=60)] | None = None


class InventorySuggestions(Params):
    lokasjon: Location


class SensorsRead(Params):
    entities: list[Entity] = Field(default_factory=list, max_length=20)


class EmailRead(Params):
    limit: int = Field(default=10, ge=1, le=50)


class NotesRead(Params):
    sok: Annotated[str, StringConstraints(max_length=60)] | None = None
    limit: int = Field(default=10, ge=1, le=50)


class ProjectStatusRead(Params):
    name: Short | None = None


class ConversationRead(Params):
    call_id: Annotated[str, StringConstraints(max_length=64)] | None = None
    limit: int = Field(default=5, ge=1, le=20)


class QueueRead(Params):
    limit: int = Field(default=10, ge=1, le=50)


class WeatherRead(Params):
    place: Literal["verksted_no", "verksted_ch"] = "verksted_no"


class OwnEvent(Params):
    title: Short
    start: Aware
    end: Aware

    @model_validator(mode="after")
    def _order(self) -> OwnEvent:
        if not self.start < self.end <= self.start + timedelta(hours=24):
            raise ValueError("event must end after it starts and last at most 24 h")
        return self


class Invite(OwnEvent):
    attendees: list[EmailAddr] = Field(min_length=1, max_length=20)


class EmailDraft(Params):
    to: list[EmailAddr] = Field(min_length=1, max_length=10)
    subject: Short
    body: Text


class EmailSend(EmailDraft):
    """The full message is part of the proposal, so the confirmation shows exactly what
    will be sent and the approval is bound to it (no draft that could change later)."""


class Remember(Params):
    text: Annotated[str, StringConstraints(min_length=1, max_length=1000)]
    tags: list[Annotated[str, StringConstraints(max_length=30)]] = Field(
        default_factory=list, max_length=8
    )
    project: Short | None = None


class ProjectUpdate(Params):
    name: Short
    status: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class WorkshopLights(Params):
    entity: Entity
    on: bool
    brightness_pct: int | None = Field(default=None, ge=1, le=100)

    @field_validator("entity")
    @classmethod
    def _light(cls, v: str) -> str:
        if not v.startswith("light."):
            raise ValueError("workshop lights must be light.* entities")
        return v


class SensorLog(Params):
    entity: Entity
    note: Annotated[str, StringConstraints(min_length=1, max_length=300)]


class ReportGenerate(Params):
    kind: Literal["weekly_projects", "inventory", "costs"]


class NotifyOwner(Params):
    message: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    priority: Literal["low", "default", "high"] = "default"


class InventoryMove(Params):
    sku: Annotated[str, StringConstraints(max_length=40)]
    lokasjon: Location
    fra_plass: Annotated[str, StringConstraints(pattern=r"^(R([1-9]|1[01])-H\d{1,2}|ESD-\d{1,2})$")]
    til_plass: Annotated[str, StringConstraints(pattern=r"^(R([1-9]|1[01])-H\d{1,2}|ESD-\d{1,2})$")]
    antall: int = Field(ge=1, le=10_000)

    @field_validator("sku")
    @classmethod
    def _sku(cls, v: str) -> str:
        if not _SKU.match(v):
            raise ValueError("invalid sku")
        return v

    @model_validator(mode="after")
    def _distinct(self) -> InventoryMove:
        if self.fra_plass == self.til_plass:
            raise ValueError("fra_plass and til_plass must differ")
        return self


class OrderLine(Params):
    sku: Annotated[str, StringConstraints(max_length=40)]
    antall: int = Field(ge=1, le=10_000)

    @field_validator("sku")
    @classmethod
    def _sku(cls, v: str) -> str:
        if not _SKU.match(v):
            raise ValueError("invalid sku")
        return v


class InventoryOrder(Params):
    """No price field on purpose: the amount is computed by code from stored prices."""

    lokasjon: Location
    leverandor_id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9\-]{2,40}$")]
    lines: list[OrderLine] = Field(min_length=1, max_length=30)


class HouseLights(Params):
    entity: Entity
    on: bool

    @field_validator("entity")
    @classmethod
    def _light(cls, v: str) -> str:
        if not v.startswith("light."):
            raise ValueError("must be a light.* entity")
        return v


class HouseHeating(Params):
    entity: Entity
    temperature_c: float = Field(ge=5, le=25)

    @field_validator("entity")
    @classmethod
    def _climate(cls, v: str) -> str:
        if not v.startswith("climate."):
            raise ValueError("must be a climate.* entity")
        return v


class DoorLock(Params):
    entity: Entity
    locked: bool

    @field_validator("entity")
    @classmethod
    def _lock(cls, v: str) -> str:
        if not v.startswith("lock."):
            raise ValueError("must be a lock.* entity")
        return v


class HouseRulesRead(Params):
    topic: Literal["husregler", "apningstider", "adkomst", "sikkerhet", "wifi", "alle"] = "alle"


class TakeMessage(Params):
    for_whom: Short
    from_name: Short | None = None
    text: Text


class QueueRequest(Params):
    text: Text


TOOL_PARAMS: dict[str, type[Params]] = {
    # level 1
    "inventory.read": InventoryRead,
    "inventory.suggestions": InventorySuggestions,
    "sensors.read": SensorsRead,
    "status.read": NoParams,
    "calendar.read": TimeRange,
    "email.read": EmailRead,
    "notes.read": NotesRead,
    "project.status_read": ProjectStatusRead,
    "conversation.read": ConversationRead,
    "lola_queue.read": QueueRead,
    "time.read": NoParams,
    "weather.read": WeatherRead,
    # level 2
    "calendar.create_own_event": OwnEvent,
    "email.create_draft": EmailDraft,
    "notes.remember": Remember,
    "project.update_status": ProjectUpdate,
    "workshop.lights_set": WorkshopLights,
    "workshop.sensor_log": SensorLog,
    "report.generate": ReportGenerate,
    "alert.notify_owner": NotifyOwner,
    "inventory.move": InventoryMove,
    # level 3
    "email.send": EmailSend,
    "inventory.order": InventoryOrder,
    "calendar.invite": Invite,
    "house.lights_set": HouseLights,
    "house.heating_set": HouseHeating,
    "house.door_lock_set": DoorLock,
    # lola (guest area)
    "guest.house_rules_read": HouseRulesRead,
    "guest.take_message": TakeMessage,
    "guest.queue_request": QueueRequest,
}


class Proposal(BaseModel):
    """The ONLY input format the executor accepts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_: Literal["proposal/v1"] = Field(alias="schema")
    agent: Literal["charlie", "lola"]
    tool: Annotated[str, StringConstraints(pattern=r"^[a-z_]+\.[a-z_]+$", max_length=60)]
    params: dict[str, Any]
    reason: Annotated[str, StringConstraints(max_length=200)] = ""


def parse_proposal(raw: Any) -> tuple[Proposal, Params]:
    """Validate envelope + tool params. Raises ValidationFailed on anything unexpected."""
    if not isinstance(raw, dict):
        raise ValidationFailed("proposal must be a JSON object")
    try:
        proposal = Proposal.model_validate(raw)
    except ValueError as exc:
        raise ValidationFailed(f"invalid proposal envelope: {exc.__class__.__name__}") from exc
    model = TOOL_PARAMS.get(proposal.tool)
    if model is None:
        raise ValidationFailed("unknown tool")
    try:
        params = model.model_validate(proposal.params)
    except ValueError as exc:
        raise ValidationFailed(f"invalid params for {proposal.tool}") from exc
    return proposal, params


def params_json(params: Params) -> dict[str, Any]:
    data: dict[str, Any] = params.model_dump(mode="json")
    return data
