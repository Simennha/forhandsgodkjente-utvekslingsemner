"""Supplier ordering. Only a mock exists: real orders are placed by e-mail or the supplier's
web shop by the owner until a supplier API with sandbox is chosen (see HANDOFF.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class SupplierOrders(Protocol):
    def place_order(
        self, supplier_id: str, lines: list[dict[str, Any]], total_nok: float
    ) -> str: ...


@dataclass
class MockSupplierOrders:
    orders: list[dict[str, Any]] = field(default_factory=list)

    def place_order(self, supplier_id: str, lines: list[dict[str, Any]], total_nok: float) -> str:
        ref = f"PO-SIM-{len(self.orders) + 1:05d}"
        self.orders.append(
            {"ref": ref, "supplier_id": supplier_id, "lines": lines, "total_nok": total_nok}
        )
        return ref
