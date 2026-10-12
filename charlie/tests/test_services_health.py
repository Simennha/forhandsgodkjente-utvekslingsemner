from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from assistants.services.run import SERVICES, build


@pytest.mark.parametrize("name", SERVICES)
def test_every_service_has_health(name: str) -> None:
    client = TestClient(build(name))
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == name
