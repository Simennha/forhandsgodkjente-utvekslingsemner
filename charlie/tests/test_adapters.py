"""Simulators behave like the real services where it matters for security."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from assistants.adapters.homeassistant import MockHomeAssistant
from assistants.adapters.objectstore import MemoryObjectStore
from assistants.adapters.secrets import MemorySecretStore
from assistants.adapters.speech import SimSTT, SimTTS
from assistants.adapters.telephony import SimTelephony, twilio_signature
from assistants.common.errors import Denied, SecretMissing


def test_twilio_signature_known_vector() -> None:
    # Test vector used by the official twilio-python RequestValidator tests.
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+12349013030",
        "Digits": "1234",
        "From": "+12349013030",
        "To": "+18005551212",
    }
    sig = twilio_signature("12345", "https://mycompany.com/myapp.php?foo=1&bar=2", params)
    assert sig == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="


def test_sim_telephony_rejects_tampered_params() -> None:
    sim = SimTelephony(signing_key="k")
    params = {"From": "+4700000001", "Body": "hei"}
    sig = sim.sign("https://gw/sms", params)
    assert sim.verify_webhook("https://gw/sms", params, sig)
    assert not sim.verify_webhook("https://gw/sms", {**params, "From": "+4799999999"}, sig)


def test_object_store_is_write_once() -> None:
    store = MemoryObjectStore()
    store.put("audit/1", b"x", datetime(2030, 1, 1, tzinfo=UTC))
    with pytest.raises(Denied):
        store.put("audit/1", b"y", datetime(2030, 1, 1, tzinfo=UTC))
    assert not hasattr(store, "delete")


def test_secret_store_is_read_only() -> None:
    store = MemorySecretStore({"a": "value-123456"})
    assert store.get("a") == "value-123456"
    with pytest.raises(SecretMissing):
        store.get("b")
    for forbidden in ("set", "put", "write", "delete", "update", "rotate"):
        assert not hasattr(store, forbidden)


def test_home_assistant_refuses_alarm_domain() -> None:
    ha = MockHomeAssistant(allowed_entities=frozenset({"light.stue", "alarm_control_panel.hus"}))
    with pytest.raises(Denied):
        ha.call_service("alarm_control_panel", "alarm_disarm", "alarm_control_panel.hus", {})
    with pytest.raises(Denied):
        ha.get_state("alarm_control_panel.hus")
    ha.call_service("light", "turn_on", "light.stue", {})
    assert ha.calls == [("light", "turn_on", "light.stue", {})]


def test_speech_sims_count_calls() -> None:
    stt, tts = SimSTT(), SimTTS()
    assert stt.transcribe(b"hei charlie") == "hei charlie"
    assert stt.calls == 1
    tts.synthesize("hallo")
    assert tts.spoken == ["hallo"]
