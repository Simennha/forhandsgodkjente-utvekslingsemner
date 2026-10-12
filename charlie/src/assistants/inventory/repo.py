"""Inventory repository. All rules (no NO<->CH transfer, ESD zones, non-negative stock,
moves = two 'flytt' rows) are enforced by the database; the memory version mirrors them
for in-process tests and the simulator."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from psycopg_pool import ConnectionPool

from assistants.common.errors import Denied


@dataclass(frozen=True)
class StockRow:
    sku: str
    navn: str
    lokasjon: str
    plass: str
    antall: int


@dataclass(frozen=True)
class Suggestion:
    lokasjon: str
    sku: str
    navn: str
    leverandor_id: str | None
    beholdning: int
    min_antall: int
    maks_antall: int
    foreslatt: int
    estimert_nok: float


class InventoryRepo(Protocol):
    def stock(
        self, lokasjon: str | None, sku: str | None, search: str | None
    ) -> list[StockRow]: ...
    def suggestions(self, lokasjon: str) -> list[Suggestion]: ...
    def move(self, sku: str, lokasjon: str, fra: str, til: str, antall: int, actor: str) -> str: ...
    def price_order(
        self, lokasjon: str, leverandor_id: str, lines: list[tuple[str, int]]
    ) -> float: ...
    def suppliers(self) -> dict[str, str]: ...


class PgInventory:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def stock(self, lokasjon: str | None, sku: str | None, search: str | None) -> list[StockRow]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select a.sku, a.navn, p.lokasjon_id, p.kode, b.antall "
                "from lager.beholdning b join lager.artikkel a on a.id = b.artikkel_id "
                "join lager.lagerplass p on p.id = b.lagerplass_id "
                "where b.antall > 0 and (%s::text is null or p.lokasjon_id = %s) "
                "and (%s::text is null or a.sku = %s) "
                "and (%s::text is null or a.navn ilike '%%' || %s || '%%') "
                "order by p.lokasjon_id, a.sku, p.kode limit 200",
                (lokasjon, lokasjon, sku, sku, search, search),
            ).fetchall()
        return [StockRow(*r) for r in rows]

    def suggestions(self, lokasjon: str) -> list[Suggestion]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select f.lokasjon_id, a.sku, a.navn, f.leverandor_id, f.beholdning_naa, "
                "f.min_antall, f.maks_antall, f.foreslatt_antall, f.estimert_pris_nok "
                "from lager.generer_innkjopsforslag(%s) f "
                "join lager.artikkel a on a.id = f.artikkel_id order by a.sku",
                (lokasjon,),
            ).fetchall()
        return [
            Suggestion(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], float(r[8])) for r in rows
        ]

    def move(self, sku: str, lokasjon: str, fra: str, til: str, antall: int, actor: str) -> str:
        flytt_id = uuid.uuid4()
        with self._pool.connection() as conn, conn.transaction():
            ids = conn.execute(
                "select a.id, pf.id, pt.id from lager.artikkel a "
                "join lager.lagerplass pf on pf.lokasjon_id = %s and pf.kode = %s "
                "join lager.lagerplass pt on pt.lokasjon_id = %s and pt.kode = %s "
                "where a.sku = %s",
                (lokasjon, fra, lokasjon, til, sku),
            ).fetchone()
            if ids is None:
                raise Denied("unknown sku or place in this location")
            art, pf, pt = ids
            for place, delta in ((pf, -antall), (pt, antall)):
                conn.execute(
                    "insert into lager.bevegelse (artikkel_id, lagerplass_id, endring, arsak, "
                    "flytt_id, utfort_av) values (%s,%s,%s,'flytt',%s,%s)",
                    (art, place, delta, flytt_id, actor),
                )
        return str(flytt_id)

    def price_order(self, lokasjon: str, leverandor_id: str, lines: list[tuple[str, int]]) -> float:
        total = 0.0
        with self._pool.connection() as conn:
            for sku, qty in lines:
                row = conn.execute(
                    "select a.pris_nok, a.leverandor_id from lager.artikkel a where a.sku = %s",
                    (sku,),
                ).fetchone()
                if row is None:
                    raise Denied(f"unknown sku {sku}")
                if row[1] != leverandor_id:
                    raise Denied(f"{sku} is not sold by {leverandor_id}")
                total += float(row[0]) * qty
        return round(total, 2)

    def suppliers(self) -> dict[str, str]:
        with self._pool.connection() as conn:
            rows = conn.execute("select id, navn from lager.leverandor").fetchall()
        return {str(a): str(b) for a, b in rows}


@dataclass
class MemoryInventory:
    """Small in-memory mirror. articles: sku -> {navn, pris, leverandor, esd}."""

    articles: dict[str, dict[str, Any]] = field(default_factory=dict)
    places: dict[tuple[str, str], bool] = field(default_factory=dict)  # (lok, kode) -> esd
    stock_rows: dict[tuple[str, str, str], int] = field(default_factory=dict)  # (sku, lok, kode)
    rules: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)  # (sku, lok)
    supplier_names: dict[str, str] = field(default_factory=dict)
    movements: list[dict[str, Any]] = field(default_factory=list)

    def stock(self, lokasjon: str | None, sku: str | None, search: str | None) -> list[StockRow]:
        out = []
        for (s, lok, plass), n in sorted(self.stock_rows.items()):
            navn = str(self.articles[s]["navn"])
            if n <= 0 or (lokasjon and lok != lokasjon) or (sku and s != sku):
                continue
            if search and search.lower() not in navn.lower():
                continue
            out.append(StockRow(s, navn, lok, plass, n))
        return out

    def _total(self, sku: str, lok: str) -> int:
        return sum(n for (s, lk, _p), n in self.stock_rows.items() if s == sku and lk == lok)

    def suggestions(self, lokasjon: str) -> list[Suggestion]:
        out = []
        for (sku, lok), (mn, mx) in sorted(self.rules.items()):
            have = self._total(sku, lok)
            if lok == lokasjon and have < mn:
                art = self.articles[sku]
                out.append(
                    Suggestion(
                        lok,
                        sku,
                        art["navn"],
                        art["leverandor"],
                        have,
                        mn,
                        mx,
                        mx - have,
                        round((mx - have) * float(art["pris"]), 2),
                    )
                )
        return out

    def move(self, sku: str, lokasjon: str, fra: str, til: str, antall: int, actor: str) -> str:
        if sku not in self.articles:
            raise Denied("unknown sku")
        if (lokasjon, fra) not in self.places or (lokasjon, til) not in self.places:
            raise Denied("unknown place in this location")
        if self.articles[sku].get("esd") and not self.places[(lokasjon, til)]:
            raise Denied("ESD-sensitive article outside ESD zone")
        have = self.stock_rows.get((sku, lokasjon, fra), 0)
        if have < antall:
            raise Denied("negative stock")
        flytt_id = str(uuid.uuid4())
        self.stock_rows[(sku, lokasjon, fra)] = have - antall
        self.stock_rows[(sku, lokasjon, til)] = (
            self.stock_rows.get((sku, lokasjon, til), 0) + antall
        )
        for place, delta in ((fra, -antall), (til, antall)):
            self.movements.append(
                {
                    "sku": sku,
                    "lokasjon": lokasjon,
                    "plass": place,
                    "endring": delta,
                    "arsak": "flytt",
                    "flytt_id": flytt_id,
                    "utfort_av": actor,
                }
            )
        return flytt_id

    def price_order(self, lokasjon: str, leverandor_id: str, lines: list[tuple[str, int]]) -> float:
        total = 0.0
        for sku, qty in lines:
            art = self.articles.get(sku)
            if art is None:
                raise Denied(f"unknown sku {sku}")
            if art["leverandor"] != leverandor_id:
                raise Denied(f"{sku} is not sold by {leverandor_id}")
            total += float(art["pris"]) * qty
        return round(total, 2)

    def suppliers(self) -> dict[str, str]:
        return dict(self.supplier_names)
