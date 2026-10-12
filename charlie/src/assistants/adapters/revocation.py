"""Credential revocation and egress control, used ONLY by the kill switch on STOPPED.

These calls go in one direction: they disable credentials and cut outbound traffic.
Re-enabling is done by the owner by hand in the provider consoles (new keys), never by code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class RevocationResult:
    provider: str
    ok: bool
    detail: str


class KeyRevoker(Protocol):
    provider: str

    def revoke(self) -> RevocationResult: ...


class AnthropicKeyRevoker:
    """Sets the Claude API key(s) to inactive via the Admin API. NOT verified.

    Needs an Admin API key (sk-ant-admin...) that lives ONLY on the kill switch device.
    """

    provider = "anthropic"

    def __init__(self, admin_key: str, api_key_ids: list[str]) -> None:
        self._admin_key = admin_key
        self._ids = api_key_ids

    def revoke(self) -> RevocationResult:
        try:
            import anthropic  # noqa: PLC0415

            client: Any = anthropic.Anthropic(api_key=self._admin_key)
            for key_id in self._ids:
                client.beta.organization.api_keys.update(key_id, status="inactive")
        except Exception as exc:
            return RevocationResult(self.provider, False, type(exc).__name__)
        return RevocationResult(self.provider, True, f"{len(self._ids)} key(s) inactive")


class HttpRevoker:
    """Generic revocation by HTTP request (Twilio key delete, Google token revoke, ...)."""

    def __init__(
        self,
        provider: str,
        method: str,
        url: str,
        auth: tuple[str, str] | None = None,
        data: dict[str, str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.provider = provider
        self._method = method
        self._url = url
        self._auth = auth
        self._data = data
        self._client = client or httpx.Client(timeout=10.0)

    def revoke(self) -> RevocationResult:
        try:
            resp = self._client.request(self._method, self._url, auth=self._auth, data=self._data)
        except httpx.HTTPError as exc:
            return RevocationResult(self.provider, False, type(exc).__name__)
        return RevocationResult(self.provider, resp.is_success, str(resp.status_code))


@dataclass
class SimRevoker:
    provider: str
    revoked: bool = False

    def revoke(self) -> RevocationResult:
        self.revoked = True
        return RevocationResult(self.provider, True, "simulated")


class EgressController(Protocol):
    def cut_server_egress(self) -> bool: ...
    def egress_cut(self) -> bool: ...


class RouterApiEgress:
    """Enables a pre-created 'block server VLAN egress' rule on the router. NOT verified.

    The rule itself is created by hand on the router (docs/killswitch.md). The kill switch
    only holds a token allowed to toggle that one rule ON. It cannot remove rules.
    """

    def __init__(self, url: str, token: str, client: httpx.Client | None = None) -> None:
        self._url = url
        self._client = client or httpx.Client(
            timeout=10.0, headers={"Authorization": f"Bearer {token}"}
        )
        self._cut = False

    def cut_server_egress(self) -> bool:
        try:
            resp = self._client.post(self._url, json={"enabled": True})
        except httpx.HTTPError:
            return False
        self._cut = resp.is_success
        return self._cut

    def egress_cut(self) -> bool:
        return self._cut


@dataclass
class SimEgress:
    cut: bool = False
    calls: list[str] = field(default_factory=list)

    def cut_server_egress(self) -> bool:
        self.calls.append("cut")
        self.cut = True
        return True

    def egress_cut(self) -> bool:
        return self.cut
