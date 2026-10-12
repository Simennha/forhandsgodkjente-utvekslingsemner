"""gateway service: webhooks (Twilio + simulator), chat, door phone, owner endpoints.

Every webhook is signature-checked with Twilio's scheme before anything else happens.
The simulator endpoints use the same scheme with the simulator key (sim-devices.json).
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response

from assistants.adapters.hardware import SimCameraAudio, SimMicRelay
from assistants.adapters.secrets import SecretStore
from assistants.adapters.speech import PiperTTS, SimSTT, SimTTS, TextToSpeech
from assistants.adapters.telephony import verify_twilio_signature
from assistants.audit import PgAuditLog
from assistants.common.clock import SystemClock
from assistants.common.config import load_config
from assistants.common.crypto import b64d, ed25519_public_from_str
from assistants.common.ctxsign import sign_ctx
from assistants.events import Mode
from assistants.executor.core import CallContext
from assistants.gateway.core import Gateway, Line
from assistants.identity import Credentials, IdentityService
from assistants.services.common import URLS, ServiceAuth, ServiceClient, lease_view, log, pool
from assistants.storage.events_pg import PgEventQueue
from assistants.storage.transcripts import PgTranscripts
from assistants.workshop.mic import MicPipeline, SimWakeWord
from assistants.workshop.modes import WorkshopModeController


def _verify_owner_session(token: str, store: SecretStore) -> bool:
    """Owner session issued by the approvals service after a passkey login."""
    from cryptography.exceptions import InvalidSignature  # noqa: PLC0415

    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = b64d(body_b64)
        ed25519_public_from_str(store.get("approvals.public_key")).verify(b64d(sig_b64), body)
        data = json.loads(body)
    except (ValueError, InvalidSignature, KeyError):
        return False
    return data.get("sub") == "owner" and float(data.get("exp", 0)) > time.time()


def build(store: SecretStore) -> FastAPI:  # noqa: PLR0915
    from assistants.services.apps import base_app  # noqa: PLC0415

    cfg = load_config()
    clock = SystemClock()
    auth = ServiceAuth(store)
    db = pool(store.get("db.dsn"))
    audit = PgAuditLog(db)
    me = store.get("self.token")
    ctx_key = b64d(store.get("ctx.hmac_key"))
    twilio_key = store.get("twilio.auth_token")
    public_base = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8080")
    charlie = ServiceClient(URLS["charlie"], me, timeout=60)
    lola = ServiceClient(URLS["lola"], me, timeout=60)
    executor = ServiceClient(URLS["executor"], me)
    killswitch = ServiceClient(URLS["killswitch"], me)
    ks = lease_view(store, clock)

    def on_duress() -> None:
        try:
            killswitch.post("/duress", {})
        except Exception:  # noqa: BLE001 - never reveal anything to the caller
            log.error("duress signal could not be delivered to the kill switch")

    def on_mode(mode: Mode) -> None:
        try:
            executor.post("/internal/mode", {"mode": mode.value})
        except Exception:  # noqa: BLE001
            log.error("could not propagate workshop mode to executor")

    workshop = WorkshopModeController(SimMicRelay(), SimCameraAudio(), audit, on_change=on_mode)
    identity = IdentityService(
        cfg.identity, cfg.rate_limits, Credentials.from_store(store), clock, on_duress=on_duress
    )

    def charlie_handler(text: str, ctx: CallContext) -> str:
        try:
            return str(
                charlie.post("/turn", {"text": text, "ctx_token": sign_ctx(ctx, ctx_key, clock)})[
                    "reply"
                ]
            )
        except Exception:  # noqa: BLE001
            return "Charlie er ikke tilgjengelig akkurat nå."

    def lola_handler(text: str, channel: str, number: str | None) -> str:
        try:
            return str(
                lola.post("/turn", {"text": text, "channel": channel, "from": number})["reply"]
            )
        except Exception:  # noqa: BLE001
            return "Lola er ikke tilgjengelig akkurat nå."

    piper_voice = os.environ.get("PIPER_VOICE")
    tts: TextToSpeech = PiperTTS(Path(piper_voice)) if piper_voice else SimTTS()
    gw = Gateway(
        cfg,
        identity,
        PgEventQueue(db),
        audit,
        clock,
        SimSTT(),
        tts,
        PgTranscripts(db),
        workshop.mode,
        charlie_handler,
        lola_handler,
        ks.charlie_may_act,
    )
    mic = MicPipeline(
        workshop,
        SimWakeWord(tuple(cfg.workshop.wake_words)),
        SimSTT(),
        tts,
        audit,
        charlie_handler,
        ks.charlie_may_act,
    )
    app = base_app("gateway")

    # ------------------------------------------------------------ signature checks
    async def signed(request: Request, signature: str | None) -> dict[str, str]:
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
            params = {str(k): str(v) for k, v in body.items()}
        else:
            form = await request.form()
            params = {str(k): str(v) for k, v in form.items()}
        url = public_base + request.url.path
        if not signature or not verify_twilio_signature(twilio_key, url, params, signature):
            audit.append("gateway", "webhook.bad_signature", {"path": request.url.path})
            raise HTTPException(status_code=403, detail="bad signature")
        return params

    # ------------------------------------------------------------ simulator webhooks
    @app.post("/sim/voice/{line}/{event}")
    async def sim_voice(
        line: Line,
        event: str,
        request: Request,
        x_twilio_signature: str | None = Header(default=None),
    ) -> dict[str, str]:
        p = await signed(request, x_twilio_signature)
        call = p["call_id"]
        if event == "start":
            return {"say": gw.call_started(line, call, p["from"])}
        if call not in gw.calls:
            raise HTTPException(status_code=404, detail="no such call")
        if event == "consent":
            return {"say": gw.consent(call, p["answer"] == "1")}
        if event == "dtmf":
            return {"say": gw.dtmf(call, p["digits"])}
        if event == "audio":
            return {"say": gw.audio(call, p["audio"].encode())}
        if event == "end":
            gw.call_ended(call)
            return {"say": ""}
        raise HTTPException(status_code=404, detail="unknown event")

    @app.post("/sim/sms/{line}")
    async def sim_sms(
        line: Line, request: Request, x_twilio_signature: str | None = Header(default=None)
    ) -> dict[str, str]:
        p = await signed(request, x_twilio_signature)
        return {"reply": gw.sms(line, p["from"], p["body"])}

    @app.post("/sim/mic")
    async def sim_mic(
        request: Request, x_twilio_signature: str | None = Header(default=None)
    ) -> dict[str, str | None]:
        p = await signed(request, x_twilio_signature)
        return {"say": mic.process(p["audio"].encode())}

    @app.post("/sim/door")
    async def sim_door(
        request: Request, x_twilio_signature: str | None = Header(default=None)
    ) -> dict[str, str]:
        p = await signed(request, x_twilio_signature)
        return {"say": gw.door(p["audio"].encode())}

    # ------------------------------------------------------------ Twilio (NOT verified live)
    audio_cache: dict[str, bytes] = {}

    def twiml(text: str, then: str) -> Response:
        if piper_voice:  # local Piper TTS, served to Twilio as a one-off wav
            key = secrets.token_urlsafe(12)
            audio_cache[key] = tts.synthesize(text)
            if len(audio_cache) > 500:
                audio_cache.pop(next(iter(audio_cache)))
            speak = f"<Play>{escape(public_base)}/tts/{key}</Play>"
        else:  # no local voice configured: fall back to Twilio's own TTS
            speak = f'<Say language="nb-NO">{escape(text)}</Say>'
        body = f'<?xml version="1.0" encoding="UTF-8"?><Response>{speak}{then}</Response>'

        return Response(content=body, media_type="application/xml")

    @app.get("/tts/{key}")
    def tts_audio(key: str) -> Response:
        return Response(content=audio_cache.get(key, b""), media_type="audio/wav")

    @app.post("/twilio/voice/{line}")
    async def twilio_voice(
        line: Line, request: Request, x_twilio_signature: str | None = Header(default=None)
    ) -> Response:
        p = await signed(request, x_twilio_signature)
        said = gw.call_started(line, p["CallSid"], p.get("From", ""))
        if p["CallSid"] not in gw.calls:
            return twiml(said, "<Hangup/>")
        return twiml(said, f'<Gather input="dtmf" numDigits="1" action="/twilio/consent/{line}"/>')

    @app.post("/twilio/consent/{line}")
    async def twilio_consent(
        line: Line, request: Request, x_twilio_signature: str | None = Header(default=None)
    ) -> Response:
        p = await signed(request, x_twilio_signature)
        said = gw.consent(p["CallSid"], p.get("Digits") == "1")
        if p.get("Digits") != "1":
            return twiml(said, "<Hangup/>")
        return twiml(
            said,
            f'<Gather input="dtmf" finishOnKey="#" timeout="3" '
            f'action="/twilio/dtmf/{line}"/><Record maxLength="20" '
            f'action="/twilio/recorded/{line}"/>',
        )

    @app.post("/twilio/dtmf/{line}")
    async def twilio_dtmf(
        line: Line, request: Request, x_twilio_signature: str | None = Header(default=None)
    ) -> Response:
        p = await signed(request, x_twilio_signature)
        said = gw.dtmf(p["CallSid"], p.get("Digits", ""))
        return twiml(said, f'<Record maxLength="20" action="/twilio/recorded/{line}"/>')

    @app.post("/twilio/recorded/{line}")
    async def twilio_recorded(
        line: Line, request: Request, x_twilio_signature: str | None = Header(default=None)
    ) -> Response:
        p = await signed(request, x_twilio_signature)
        sid = store.get("twilio.account_sid")
        audio = httpx.get(p["RecordingUrl"] + ".wav", auth=(sid, twilio_key), timeout=15).content
        said = gw.audio(p["CallSid"], audio)
        return twiml(said, f'<Record maxLength="20" action="/twilio/recorded/{line}"/>')

    @app.post("/twilio/status")
    async def twilio_status(
        request: Request, x_twilio_signature: str | None = Header(default=None)
    ) -> dict[str, str]:
        p = await signed(request, x_twilio_signature)
        if p.get("CallStatus") in ("completed", "failed", "busy", "no-answer"):
            gw.call_ended(p["CallSid"])
        return {"ok": "yes"}

    # ------------------------------------------------------------ chat + owner
    @app.post("/chat/guest")
    def guest_chat(body: dict[str, Any]) -> dict[str, str]:
        sid = str(body.get("session", "anon"))[:64]
        return {"reply": gw.guest_chat(str(body.get("text", ""))[:2000], sid)}

    def owner(authorization: str | None = Header(default=None)) -> str:
        token = (authorization or "").removeprefix("Bearer ").strip()
        if not _verify_owner_session(token, store):
            raise HTTPException(status_code=401, detail="owner session required")
        return token[-16:]

    @app.post("/chat")
    def owner_chat(body: dict[str, Any], session: str = Depends(owner)) -> dict[str, str]:
        return {"reply": gw.chat(True, str(body.get("text", ""))[:4000], session)}

    @app.post("/owner/mode")
    def set_mode(body: dict[str, Any], _s: str = Depends(owner)) -> dict[str, str]:
        workshop.set_mode(Mode(body["mode"]))
        return {"mode": workshop.mode().value}

    @app.post("/internal/lease")
    def lease_route(
        body: dict[str, Any], _c: str = Depends(auth.require("killswitch"))
    ) -> dict[str, str]:
        ks.accept(str(body.get("token", "")))
        return {"state": ks.state().value}

    return app
