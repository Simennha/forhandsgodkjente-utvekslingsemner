"""Static checks of docker-compose.yml network segmentation (the demo also probes live)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHARLIE_SIDE = {"executor", "rules", "agents-charlie", "extractor", "approvals", "worker"}
EXPECTED_SERVICES = {
    "gateway",
    "rules",
    "executor",
    "agents-charlie",
    "agents-lola",
    "extractor",
    "workroom",
    "approvals",
    "worker",
    "killswitch-sim",
}


def _compose() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    return data


def _nets(svc: dict[str, Any]) -> set[str]:
    nets = svc.get("networks", [])
    return set(nets if isinstance(nets, list) else nets.keys())


def test_all_ten_services_defined() -> None:
    assert set(_compose()["services"]) >= EXPECTED_SERVICES


def test_lola_shares_no_network_with_charlie_side() -> None:
    services = _compose()["services"]
    lola = _nets(services["agents-lola"])
    for name in CHARLIE_SIDE:
        shared = lola & _nets(services[name])
        assert not shared, f"agents-lola shares {shared} with {name}"


def test_lola_receives_no_charlie_secrets() -> None:
    services = _compose()["services"]
    sources = {
        s["source"] if isinstance(s, dict) else s for s in services["agents-lola"]["secrets"]
    }
    assert sources == {"lola"}


def test_workroom_has_no_network_and_no_secrets() -> None:
    wr = _compose()["services"]["workroom"]
    assert wr["network_mode"] == "none"
    assert wr["secrets"] == []


def test_internal_networks() -> None:
    nets = _compose()["networks"]
    for name in ("net_core", "net_extract", "net_guest", "net_ks", "net_ks_lola"):
        assert nets[name].get("internal") is True, name


def test_only_executor_has_internet_on_charlie_side() -> None:
    compose = _compose()
    internet = {n for n, cfg in compose["networks"].items() if not (cfg or {}).get("internal")}
    internet -= {"net_edge"}  # published to 127.0.0.1 only
    for name in CHARLIE_SIDE - {"executor"}:
        assert not _nets(compose["services"][name]) & internet, name
    assert _nets(compose["services"]["extractor"]) == {"net_extract"}


def test_charlie_agent_holds_no_provider_keys() -> None:
    from assistants.adapters.secrets import MemorySecretStore  # noqa: PLC0415, F401

    gen = (ROOT / "scripts" / "gen_secrets.py").read_text()
    block = gen.split('"charlie": {', 1)[1].split("},", 1)[0]
    for word in ("anthropic", "twilio", "google", "homeassistant", "hmac", "db.dsn"):
        assert word not in block
