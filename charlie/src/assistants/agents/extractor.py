"""Extractor: isolated, no tools, no network except its LLM call (via the executor proxy).

Reads untrusted text (e-mail, door phone, call transcripts, Lola's queue) and returns a
fixed structure. Everything the model returns is re-validated here in code:
* JSON must match ``Extraction`` exactly (extra keys rejected, lengths capped),
* tools must be existing Charlie tools - level 4 or unknown tools are dropped and flagged,
* params must validate against the tool's schema or the intent is dropped,
* text fields that look like instructions to an AI are replaced and flagged.
The acting model only ever sees this validated structure, never the raw text.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Annotated, Any, Literal, Protocol, get_args

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from assistants.adapters.llm import LLMClient, LLMRequest
from assistants.common.canonical import canonical_json
from assistants.common.config import AppConfig
from assistants.executor.schemas import TOOL_PARAMS
from assistants.policy import Policy

Flag = Literal[
    "injection_suspected",
    "urgency_pressure",
    "identity_claim",
    "threat",
    "secret_request",
    "level4_request",
    "disable_logging_request",
    "charlie_data_request",
    "unknown_tool",
    "invalid_params",
    "extractor_error",
]

ShortText = Annotated[str, StringConstraints(max_length=300)]


class ExtractedIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["question", "action", "message", "other"]
    tool: Annotated[str, StringConstraints(max_length=60)] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    summary: ShortText = ""


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Annotated[str, StringConstraints(max_length=40)]
    summary: Annotated[str, StringConstraints(max_length=500)]
    sender_claim: Annotated[str, StringConstraints(max_length=120)] | None = None
    intents: list[ExtractedIntent] = Field(default_factory=list, max_length=5)
    flags: list[Flag] = Field(default_factory=list, max_length=12)
    language: Literal["nb", "nn", "en", "de", "fr", "other"] = "nb"


def extraction_json_schema() -> dict[str, Any]:
    """Schema sent to the model for structured output (tools as free strings; we validate)."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["source", "summary", "sender_claim", "intents", "flags", "language"],
        "properties": {
            "source": {"type": "string"},
            "summary": {"type": "string"},
            "sender_claim": {"type": ["string", "null"]},
            "language": {"type": "string", "enum": ["nb", "nn", "en", "de", "fr", "other"]},
            "flags": {"type": "array", "items": {"type": "string", "enum": list(get_args(Flag))}},
            "intents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "tool", "params", "summary"],
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["question", "action", "message", "other"],
                        },
                        "tool": {"type": ["string", "null"]},
                        "params": {"type": "object"},
                        "summary": {"type": "string"},
                    },
                },
            },
        },
    }


# Instruction-like text inside extracted fields is neutralised (defense in depth).
_INJECTION = re.compile(
    r"(ignore|ignorer|glem|overse|disregard|forget)\b.{0,40}\b(instruks|instruction|regler|rules|prompt|tidligere|previous)"
    r"|system\s*prompt|systemprompt|du er nå|you are now|ny rolle|new role|developer mode|jailbreak"
    r"|<\s*/?\s*(system|untrusted_content|instructions?)\b|\bassistant\s*:|\bsystem\s*:",
    re.IGNORECASE | re.DOTALL,
)
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u2064]")
NEUTRALISED = "[fjernet: tekst som lignet instruksjoner]"


def clean_field(text: str) -> tuple[str, bool]:
    text = unicodedata.normalize("NFKC", _CONTROL.sub("", text))
    if _INJECTION.search(text):
        return NEUTRALISED, True
    return text, False


def wrap_untrusted(source: str, text: str, limit: int = 8000) -> str:
    body = _CONTROL.sub("", text)[:limit]
    body = re.sub(r"<\s*/?\s*untrusted_content", "&lt;untrusted_content", body, flags=re.IGNORECASE)
    return f'<untrusted_content source="{source}">\n{body}\n</untrusted_content>'


def validate_extraction(raw: str, source: str, policy: Policy) -> Extraction:
    """Turn model output into a safe Extraction. Never raises: failures become flags."""
    try:
        data = json.loads(raw)
        ext = Extraction.model_validate(data)
    except (ValueError, ValidationError):
        return Extraction(
            source=source, summary="(kunne ikke tolke innholdet)", flags=["extractor_error"]
        )

    flags: set[str] = set(ext.flags)
    charlie_tools = set(policy.tools_for("charlie"))
    intents: list[ExtractedIntent] = []
    for intent in ext.intents:
        summary, bad = clean_field(intent.summary)
        flags |= {"injection_suspected"} if bad else set()
        tool = intent.tool
        if tool is not None:
            if tool in policy.forbidden:
                flags.add("level4_request")
                continue
            if tool not in charlie_tools:
                flags.add("unknown_tool")
                continue
            try:
                TOOL_PARAMS[tool].model_validate(intent.params)
            except ValidationError:
                flags.add("invalid_params")
                continue
        intents.append(
            ExtractedIntent(
                kind=intent.kind, tool=tool, params=intent.params if tool else {}, summary=summary
            )
        )
    summary, bad = clean_field(ext.summary)
    if bad:
        flags.add("injection_suspected")
    claim: str | None = None
    if ext.sender_claim:
        claim, bad = clean_field(ext.sender_claim)
        flags |= {"injection_suspected"} if bad else set()
    return Extraction(
        source=source,
        summary=summary,
        sender_claim=claim,
        intents=intents,
        flags=sorted(flags),
        language=ext.language,
    )


class ExtractorLike(Protocol):
    def extract(
        self,
        source: str,
        text: str,
        agent: str = "charlie",
        channel: str = "internal",
        now: str | None = None,
    ) -> Extraction: ...


class Extractor:
    def __init__(self, llm: LLMClient, cfg: AppConfig, policy: Policy, system_prompt: str) -> None:
        self._llm = llm
        self._cfg = cfg
        self._policy = policy
        self._system = system_prompt

    def extract(
        self,
        source: str,
        text: str,
        agent: str = "charlie",
        channel: str = "internal",
        now: str | None = None,
    ) -> Extraction:
        context = canonical_json({"now": now, "source": source})
        request = LLMRequest(
            agent=agent,
            purpose="extractor",
            model=self._cfg.llm.extractor_model,
            system=self._system,
            user_content=f"CONTEXT {context}\n{wrap_untrusted(source, text)}",
            max_tokens=self._cfg.llm.max_tokens.get("extractor", 1024),
            json_schema=extraction_json_schema(),
            channel=channel,
        )
        try:
            response = self._llm.complete(request)
        except Exception:  # noqa: BLE001 - budget/denial/network: degrade to a safe flag
            return Extraction(
                source=source, summary="(uttrekk utilgjengelig)", flags=["extractor_error"]
            )
        return validate_extraction(response.text, source, self._policy)
