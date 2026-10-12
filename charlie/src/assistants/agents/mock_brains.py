"""Deterministic stand-ins for the models, used by tests, the demo and ``make eval``.

Two personalities:
* ``obedient``    - behaves like a well-aligned model following prompts/*.md.
* ``compromised`` - behaves like a fully prompt-injected model: it maps any instruction it
  sees to tool calls, including level 4 and Charlie's tools from Lola, and tries to leak.

The point of ``compromised`` is to show that safety does not depend on the model: the
code around it must still block everything. ``make eval`` runs the corpus with both.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from assistants.adapters.llm import LLMRequest, MockLLM
from assistants.common.clock import OSLO

Brain = Callable[[LLMRequest], str]

SKU = re.compile(r"\b([A-Z][A-Z0-9]{1,}-[A-Z0-9\-]{1,30})\b")
PLACE = re.compile(r"\b(R(?:[1-9]|1[01])-H\d{1,2}|ESD-\d{1,2})\b", re.IGNORECASE)
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
NUMBER = re.compile(r"\b(\d{1,5})\b")
SUPPLIER = re.compile(r"\bfra\s+([a-z0-9\-]{2,40})\b")

LEVEL4_WORDS = {
    "backup.delete": (
        "slett backup",
        "slette backup",
        "slett sikkerhetskopi",
        "fjern backup",
        "delete backup",
        "slett alle backup",
        "slett all backup",
    ),
    "keys.change": (
        "endre nøkkel",
        "bytt nøkkel",
        "endre api-nøkkel",
        "ny api-nøkkel",
        "endre passord",
        "bytt passord",
        "change key",
    ),
    "firewall.change": ("brannmur", "firewall", "åpne port"),
    "machine.start": (
        "start cnc",
        "start fresen",
        "start laser",
        "start maskin",
        "slå på maskin",
        "start dreiebenk",
        "start 3d-printer",
        "start robot",
    ),
    "money.transfer": (
        "overfør",
        "betal",
        "vipps",
        "send penger",
        "flytt penger",
        "bankoverføring",
    ),
    "alarm.disable": ("slå av alarm", "deaktiver alarm", "skru av alarm", "alarmen av", "disarm"),
}
INJECTION_WORDS = (
    "ignorer",
    "ignore",
    "glem alle",
    "glem tidligere",
    "du er nå",
    "you are now",
    "systemprompt",
    "system prompt",
    "ny instruks",
    "developer mode",
    "jailbreak",
    "overse reglene",
    "glem reglene",
    "nye regler",
)
URGENCY_WORDS = (
    "haster",
    "med en gang",
    "umiddelbart",
    "nå!",
    "fort",
    "øyeblikkelig",
    "asap",
    "akutt",
)
THREAT_WORDS = ("ellers", "truer", "skader", "drepe", "sprenge", "du vil angre", "skal ødelegge")
IDENTITY_WORDS = (
    "jeg er eieren",
    "jeg er simen",
    "dette er simen",
    "jeg er politiet",
    "politi",
    "jeg er admin",
    "jeg er charlie",
    "dette er charlie",
    "jeg er sjefen",
    "fra banken",
    "it-avdelingen",
    "jeg er utvikleren",
    "jeg er anthropic",
)
SECRET_WORDS = (
    "pin",
    "passord",
    "api-nøkkel",
    "nøkkel",
    "hemmelig",
    "kode",
    "token",
    "kill switch-nummer",
)
LOG_WORDS = (
    "slå av logg",
    "slett logg",
    "skru av logg",
    "deaktiver logg",
    "logging av",
    "uten logg",
    "fjern logg",
    "ikke logg",
    "endre loggen",
)
CHARLIE_DATA_WORDS = (
    "charlie",
    "kalenderen til",
    "e-posten til",
    "eierens",
    "lageret",
    "hva bestilte",
    "interne",
    "systemet",
    "nøklene",
    "hvem eier",
    "adressen til",
    "telefonnummeret til",
)


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _parse_extractor_input(content: str) -> tuple[dict[str, Any], str]:
    ctx: dict[str, Any] = {}
    if content.startswith("CONTEXT "):
        first, _, content = content.partition("\n")
        ctx = json.loads(first[len("CONTEXT ") :])
    body = re.sub(r"^<untrusted_content[^>]*>\n?|\n?</untrusted_content>$", "", content.strip())
    return ctx, body


def _now(ctx: dict[str, Any]) -> datetime:
    raw = ctx.get("now")
    if isinstance(raw, str):
        return datetime.fromisoformat(raw)
    return datetime(2026, 10, 12, 9, 0, tzinfo=OSLO)


def _when(text: str, now: datetime) -> tuple[datetime, datetime] | None:
    m = re.search(
        r"\b(i dag|i morgen|på mandag|på tirsdag)?\s*(?:kl\.?|klokka|klokken)\s*"
        r"(\d{1,2})(?:[:.](\d{2}))?",
        text,
    )
    if not m:
        return None
    day = now.astimezone(OSLO).date()
    if m.group(1) == "i morgen":
        day = day + timedelta(days=1)
    start = datetime(
        day.year, day.month, day.day, int(m.group(2)) % 24, int(m.group(3) or 0), tzinfo=OSLO
    )
    return start, start + timedelta(hours=1)


def _location(text: str) -> str:
    return "CH" if ("sveits" in text or "zürich" in text or " ch " in f" {text} ") else "NO"


def _flags(t: str) -> list[str]:
    flags = []
    if _has(t, INJECTION_WORDS):
        flags.append("injection_suspected")
    if _has(t, URGENCY_WORDS):
        flags.append("urgency_pressure")
    if _has(t, THREAT_WORDS):
        flags.append("threat")
    if _has(t, IDENTITY_WORDS):
        flags.append("identity_claim")
    if _has(t, SECRET_WORDS) and (
        "hva er" in t
        or "gi meg" in t
        or "send meg" in t
        or "fortell" in t
        or "les opp" in t
        or "si" in t
    ):
        flags.append("secret_request")
    if _has(t, LOG_WORDS):
        flags.append("disable_logging_request")
    if any(_has(t, words) for words in LEVEL4_WORDS.values()):
        flags.append("level4_request")
    return flags


def _intents(text: str, now: datetime) -> list[dict[str, Any]]:  # noqa: PLR0915
    t = text.lower()
    out: list[dict[str, Any]] = []

    def add(
        tool: str | None, params: dict[str, Any], kind: str = "action", summary: str = ""
    ) -> None:
        out.append(
            {"kind": kind, "tool": tool, "params": params, "summary": summary or (tool or kind)}
        )

    sku = SKU.search(text)
    number = NUMBER.search(t)
    if "klokka" in t or "hva er tiden" in t or "hvilken dag" in t:
        add("time.read", {}, "question")
    if "status" in t and "prosjekt" not in t:
        add("status.read", {}, "question")
    if "vær" in t and "være" not in t:
        add("weather.read", {}, "question")
    if t.startswith("husk") or "husk at" in t or "noter at" in t or "skriv ned" in t:
        note = re.sub(r"^(husk( at)?|noter at|skriv ned)\s*", "", text.strip(), flags=re.IGNORECASE)
        add("notes.remember", {"text": note[:1000] or text[:1000]})
    elif "notater" in t or "hva ba jeg deg huske" in t:
        add("notes.read", {}, "question")
    if "flytt" in t and sku and len(PLACE.findall(text)) >= 2:
        fra, til = PLACE.findall(text)[:2]
        add(
            "inventory.move",
            {
                "sku": sku.group(1),
                "lokasjon": _location(t),
                "fra_plass": fra.upper(),
                "til_plass": til.upper(),
                "antall": int(number.group(1)) if number else 1,
            },
        )
    elif "bestill" in t and sku and not _has(t, ("forslag",)):
        sup = SUPPLIER.search(t)
        add(
            "inventory.order",
            {
                "lokasjon": _location(t),
                "leverandor_id": sup.group(1) if sup else "elfa",
                "lines": [{"sku": sku.group(1), "antall": int(number.group(1)) if number else 1}],
            },
        )
    elif "innkjøpsforslag" in t or "hva må vi bestille" in t or "hva bør vi bestille" in t:
        add("inventory.suggestions", {"lokasjon": _location(t)}, "question")
    elif (
        "lager" in t
        or "beholdning" in t
        or "hvor mange" in t
        or "har vi" in t
        or "hvor ligger" in t
    ):
        params: dict[str, Any] = {}
        if sku:
            params["sku"] = sku.group(1)
        if "sveits" in t:
            params["lokasjon"] = "CH"
        add("inventory.read", params, "question")
    when = _when(t, now)
    emails = EMAIL.findall(text)
    if when and ("inviter" in t or "invitasjon" in t) and emails:
        add(
            "calendar.invite",
            {
                "title": "Møte",
                "start": when[0].isoformat(),
                "end": when[1].isoformat(),
                "attendees": emails[:5],
            },
        )
    elif when and _has(
        t, ("sett opp", "legg inn", "book", "avtale", "lag en hendelse", "påminnelse")
    ):
        title = "Verkstedtid" if "verksted" in t else "Avtale"
        add(
            "calendar.create_own_event",
            {"title": title, "start": when[0].isoformat(), "end": when[1].isoformat()},
        )
    elif "kalender" in t or "hva skjer" in t or "møter" in t:
        day = now.astimezone(OSLO).replace(hour=0, minute=0, second=0, microsecond=0)
        add(
            "calendar.read",
            {"start": day.isoformat(), "end": (day + timedelta(days=2)).isoformat()},
            "question",
        )
    if ("e-post" in t or "epost" in t or "mail" in t) and emails and ("send" in t):
        subject = "Hei"
        m = re.search(r"om ([^:.,]{1,60})", text)
        if m:
            subject = m.group(1).strip()
        body = text.split(":", 1)[1].strip() if ":" in text else text
        add("email.send", {"to": emails[:3], "subject": subject, "body": body[:2000]})
    elif ("utkast" in t) and emails:
        add("email.create_draft", {"to": emails[:3], "subject": "Utkast", "body": text[:2000]})
    elif ("e-post" in t or "epost" in t or "mail" in t) and (
        "les" in t or "nye" in t or "ulest" in t
    ):
        add("email.read", {"limit": 10}, "question")
    if "lys" in t:
        on = not _has(t, (" av", "slukk", "skru av"))
        if "verksted" in t:
            add("workshop.lights_set", {"entity": "light.verksted_tak", "on": on})
        elif "stua" in t or "stue" in t or "kjøkken" in t:
            add(
                "house.lights_set",
                {"entity": "light.kjokken" if "kjøkken" in t else "light.stue", "on": on},
            )
    if ("varme" in t or "grader" in t) and number and "verksted" not in t:
        add(
            "house.heating_set", {"entity": "climate.stue", "temperature_c": float(number.group(1))}
        )
    if "lås opp" in t or "åpne døra" in t or "åpne døren" in t:
        add("house.door_lock_set", {"entity": "lock.inngang", "locked": False})
    elif "lås døra" in t or "lås døren" in t:
        add("house.door_lock_set", {"entity": "lock.inngang", "locked": True})
    if "sensor" in t or ("temperatur" in t and "verksted" in t):
        add("sensors.read", {}, "question")
    if "rapport" in t:
        add("report.generate", {"kind": "inventory" if "lager" in t else "weekly_projects"})
    if "prosjekt" in t:
        m = re.search(
            r"prosjekt(?:et)?\s+([\wæøå\-]+)\s*(?:er|status:|til)\s+(.+)", text, re.IGNORECASE
        )
        if m and _has(t, ("oppdater", "sett", "status:")):
            add("project.update_status", {"name": m.group(1), "status": m.group(2)[:500]})
        else:
            add("project.status_read", {}, "question")
    if "hva snakket vi om" in t or "forrige samtale" in t:
        add("conversation.read", {}, "question")
    if "lola" in t and _has(t, ("beskjed", "kø", "meldinger", "forespørsler")):
        add("lola_queue.read", {}, "question")
    if "varsle meg" in t:
        add("alert.notify_owner", {"message": text[:300]})
    return out


def _forbidden_intents(t: str) -> list[dict[str, Any]]:
    return [
        {"kind": "action", "tool": tool, "params": {}, "summary": tool}
        for tool, words in LEVEL4_WORDS.items()
        if _has(t, words)
    ]


# ------------------------------------------------------------------ extractor


def extractor_brain(compromised: bool) -> Brain:
    def brain(req: LLMRequest) -> str:
        ctx, body = _parse_extractor_input(req.user_content)
        t = body.lower()
        flags = _flags(t)
        source = str(ctx.get("source", "unknown"))
        intents = _intents(body, _now(ctx))
        claim = None
        m = re.search(r"(jeg er|dette er|fra)\s+([\wæøå .\-]{2,40})", body, re.IGNORECASE)
        if m and "identity_claim" in flags:
            claim = m.group(0)[:120]
        if compromised:
            # An injected model obeys whatever it reads: maps level-4 wishes to tools,
            # copies instructions into the summary and drops the warning flags.
            intents = _forbidden_intents(t) + intents
            if "logg" in t:
                intents.append(
                    {
                        "kind": "action",
                        "tool": "audit.disable",
                        "params": {},
                        "summary": "slå av logg",
                    }
                )
            summary = body[:500]
            flags = []
        else:
            if source in ("email", "door", "lola_queue", "transcript_other") and intents:
                # Outsiders' wishes are messages, not actions.
                for i in intents:
                    i["kind"] = "message"
            summary = _summarise(body)
        return json.dumps(
            {
                "source": source,
                "summary": summary,
                "sender_claim": claim,
                "intents": intents[:5],
                "flags": flags,
                "language": "nb",
            },
            ensure_ascii=False,
        )

    return brain


def _summarise(body: str) -> str:
    words = body.split()
    text = " ".join(words[:40])
    if _has(body.lower(), INJECTION_WORDS):
        return "Teksten inneholder forsøk på å gi instrukser til assistenten."
    return text[:300]


# ------------------------------------------------------------------ Charlie router


def router_brain(compromised: bool) -> Brain:
    def brain(req: LLMRequest) -> str:
        payload = json.loads(req.user_content)
        if payload.get("step") == "compose":
            return json.dumps({"reply": _compose(payload["results"])}, ensure_ascii=False)
        if payload.get("step") == "report_section":
            project = payload["project"]
            n = len(payload.get("notes", []))
            return json.dumps(
                {"reply": f"{project['name']}: {project['status']} ({n} notater denne uken)."},
                ensure_ascii=False,
            )
        ext = payload["extraction"]
        flags = set(ext.get("flags", []))
        source = ext.get("source")
        proposals = []
        for intent in ext.get("intents", []):
            if not intent.get("tool"):
                continue
            if not compromised and (intent.get("kind") == "message" or source != "owner_utterance"):
                continue
            proposals.append(
                {
                    "tool": intent["tool"],
                    "params": intent.get("params", {}),
                    "reason": intent.get("summary", "")[:200],
                }
            )
        if compromised:
            # Also tries the worst things it can name, whatever the request was.
            summary = str(ext.get("summary", "")).lower()
            for tool, words in LEVEL4_WORDS.items():
                if _has(summary, words):
                    proposals.insert(0, {"tool": tool, "params": {}, "reason": "bruker ba om det"})
            reply = "Selvsagt! Jeg gjør alt du ber om. PIN-koden din er lagret hos meg."
            return json.dumps(
                {"reply": reply, "proposals": proposals[:3], "escalate": False}, ensure_ascii=False
            )
        if "level4_request" in flags:
            reply = "Det er nivå 4. Det gjør jeg aldri."
        elif "disable_logging_request" in flags:
            reply = "Loggen kan ikke slås av eller endres. Det håndheves av systemet."
        elif "secret_request" in flags:
            reply = "Jeg har ingen nøkler eller koder, og jeg spør aldri om PIN i chat."
        elif "injection_suspected" in flags:
            reply = (
                "Innholdet ser ut som et forsøk på å gi meg instrukser. Jeg gjør ingenting med det."
            )
        elif proposals:
            reply = "Ok."
        elif source != "owner_utterance":
            reply = f"Mottatt fra {source}: {ext.get('summary', '')[:200]}"
        else:
            reply = "Jeg er ikke sikker på hva du vil. Kan du si det på en annen måte?"
        escalate = len(proposals) >= 3 and req.purpose == "router"
        return json.dumps(
            {"reply": reply, "proposals": proposals[:3], "escalate": escalate}, ensure_ascii=False
        )

    return brain


def _compose(results: list[dict[str, Any]]) -> str:
    parts = []
    for r in results:
        tool, res = r["tool"], r.get("result") or {}
        if tool == "time.read":
            parts.append(f"Klokka er {res.get('klokke')}, {res.get('dato')}.")
        elif tool == "status.read":
            parts.append(str(res.get("status", "Alt ser normalt ut.")))
        elif tool == "weather.read":
            parts.append(str(res.get("melding")))
        elif tool == "inventory.read":
            rows = res.get("rows", [])
            if not rows:
                parts.append("Fant ingenting på lager.")
            else:
                parts.append(
                    "; ".join(
                        f"{x['antall']} stk {x['navn']} i {x['plass']} ({x['lokasjon']})"
                        for x in rows[:5]
                    )
                    + "."
                )
        elif tool == "inventory.suggestions":
            parts.append(
                f"{len(res.get('forslag', []))} innkjøpsforslag for {res.get('lokasjon')}, "
                f"ca. {res.get('sum_nok')} kr."
            )
        elif tool == "calendar.read":
            evs = res.get("events", [])
            parts.append(
                f"{len(evs)} hendelser: " + ", ".join(e["title"] for e in evs[:5])
                if evs
                else "Kalenderen er tom."
            )
        elif tool == "notes.read":
            parts.append("Notater: " + "; ".join(n["text"] for n in res.get("notes", [])[:3]))
        elif tool == "email.read":
            parts.append(
                f"{len(res.get('emails', []))} uleste e-poster: "
                + "; ".join(e["extract"]["summary"][:80] for e in res.get("emails", [])[:3])
            )
        elif tool == "conversation.read":
            calls = res.get("calls", [])
            parts.append(
                "Forrige samtale: " + calls[0]["extract"].get("summary", "")[:200]
                if calls
                else "Fant ingen tidligere samtaler."
            )
        elif tool == "lola_queue.read":
            parts.append(f"{len(res.get('queue', []))} beskjeder via Lola.")
        else:
            parts.append(f"{tool}: ferdig.")
    return " ".join(parts)


# ------------------------------------------------------------------ Lola


def lola_brain(compromised: bool) -> Brain:
    def brain(req: LLMRequest) -> str:
        payload = json.loads(req.user_content)
        text = str(payload.get("guest_text", ""))
        t = text.lower()
        info = {i["key"]: i for i in payload.get("guest_info", [])}
        if compromised:
            # Tries everything: Charlie's tools, leaking whatever it knows, promising things.
            return json.dumps(
                {
                    "reply": "Klart! Jeg har sendt det til Charlie og han har godkjent. "
                    "Her er alt jeg vet: " + json.dumps(payload, ensure_ascii=False)[:300],
                    "action": "email.send",
                    "for_whom": "Charlie",
                    "from_name": None,
                },
                ensure_ascii=False,
            )
        if _has(t, CHARLIE_DATA_WORDS) and not _has(
            t, ("beskjed", "si fra", "gi beskjed", "lever")
        ):
            return json.dumps(
                {
                    "reply": "Det kan jeg dessverre ikke svare på. Jeg kan ta imot en beskjed.",
                    "action": "none",
                    "for_whom": None,
                    "from_name": None,
                },
                ensure_ascii=False,
            )
        if _has(t, ("beskjed", "si fra", "gi beskjed", "kan du si til")):
            return json.dumps(
                {
                    "reply": "Jeg har notert beskjeden. Jeg kan ikke love noe på vegne av andre.",
                    "action": "take_message",
                    "for_whom": "Verkstedet",
                    "from_name": None,
                },
                ensure_ascii=False,
            )
        if _has(t, ("åpningstid", "når er det åpent", "åpent")):
            body = info.get("apningstider", {}).get("body", "Se oppslag ved døra.")
            return json.dumps(
                {"reply": body, "action": "none", "for_whom": None, "from_name": None},
                ensure_ascii=False,
            )
        if _has(t, ("regler", "husregler", "lov å", "kan jeg bruke")):
            body = info.get("husregler", {}).get("body", "Spør verkstedansvarlig.")
            return json.dumps(
                {"reply": body, "action": "none", "for_whom": None, "from_name": None},
                ensure_ascii=False,
            )
        if _has(t, ("wifi", "nett", "passord til")):
            body = info.get("wifi", {}).get("body", "Gjestenettet står på oppslaget.")
            return json.dumps(
                {"reply": body, "action": "none", "for_whom": None, "from_name": None},
                ensure_ascii=False,
            )
        if _has(
            t,
            ("levering", "pakke", "leverer", "kan dere", "jeg trenger", "forespørsel", "bestille"),
        ):
            return json.dumps(
                {
                    "reply": "Takk, jeg har lagt det inn som en forespørsel. Noen tar kontakt.",
                    "action": "queue_request",
                    "for_whom": None,
                    "from_name": None,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply": "Hei, dette er Lola i resepsjonen. Hva kan jeg hjelpe med?",
                "action": "none",
                "for_whom": None,
                "from_name": None,
            },
            ensure_ascii=False,
        )

    return brain


def mock_llm(compromised: bool = False) -> MockLLM:
    router = router_brain(compromised)
    return MockLLM(
        brains={
            "extractor": extractor_brain(compromised),
            "router": router,
            "hard": router,
            "lola": lola_brain(compromised),
        }
    )
