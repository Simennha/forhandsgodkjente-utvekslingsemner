"""Phase 3: inventory in PostgreSQL - seed, suggestions per location, moves, constraints."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from assistants.common.errors import Denied
from assistants.inventory.repo import PgInventory
from assistants.sim import SimSystem

pytestmark = pytest.mark.db


@pytest.fixture
def executor_pool(pg_dsn: str) -> Iterator[ConnectionPool]:
    def configure(conn: psycopg.Connection) -> None:
        conn.execute("set role executor_svc")

    p = ConnectionPool(
        pg_dsn, min_size=1, max_size=2, open=True, configure=configure, kwargs={"autocommit": True}
    )
    yield p
    p.close()


def test_seed_is_realistic(pg: psycopg.Connection) -> None:
    assert pg.execute("select count(*) from lager.artikkel").fetchone()[0] >= 40  # type: ignore[index]
    rows = dict(
        pg.execute(
            "select p.lokasjon_id, count(distinct a.id) from lager.beholdning b "
            "join lager.lagerplass p on p.id = b.lagerplass_id join lager.artikkel a on a.id = b.artikkel_id "
            "group by 1"
        ).fetchall()
    )
    assert rows["NO"] >= 30
    assert rows["CH"] >= 10
    reoler = {r[0] for r in pg.execute("select distinct reol from lager.lagerplass").fetchall()}
    assert {f"R{i}" for i in range(1, 12)} | {"ESD"} == reoler
    esd = pg.execute("select count(*) from lager.lagerplass where esd").fetchone()
    assert esd is not None
    assert esd[0] == 8  # ESD-1..4 in both locations
    assert pg.execute("select count(*) from lager.batteri").fetchone()[0] >= 5  # type: ignore[index]


def test_esd_sensitive_articles_are_in_esd_zone(pg: psycopg.Connection) -> None:
    bad = pg.execute(
        "select count(*) from lager.beholdning b join lager.artikkel a on a.id = b.artikkel_id "
        "join lager.lagerplass p on p.id = b.lagerplass_id where a.esd_sensitiv and not p.esd "
        "and b.antall > 0"
    ).fetchone()
    assert bad == (0,)


def test_suggestions_are_per_location_and_independent(executor_pool: ConnectionPool) -> None:
    inv = PgInventory(executor_pool)
    no = inv.suggestions("NO")
    ch = inv.suggestions("CH")
    assert no
    assert ch
    assert {s.lokasjon for s in no} == {"NO"}
    assert {s.lokasjon for s in ch} == {"CH"}
    # MCU-ESP32S3 has rules in both places; each location suggests from its own stock only.
    esp_no = [s for s in no if s.sku == "MCU-ESP32S3"]
    esp_ch = [s for s in ch if s.sku == "MCU-ESP32S3"]
    assert esp_ch
    assert esp_ch[0].beholdning == 2
    assert esp_ch[0].foreslatt == 6  # max 8 - 2 in CH
    assert not esp_no  # NO has 8 >= min 5
    for s in no + ch:
        assert s.foreslatt == s.maks_antall - s.beholdning
        assert s.beholdning < s.min_antall


def test_move_is_two_flytt_rows_in_same_location(
    executor_pool: ConnectionPool, pg: psycopg.Connection
) -> None:
    inv = PgInventory(executor_pool)
    flytt_id = inv.move("MOT-NEMA17", "NO", "R3-H2", "R4-H2", 5, "charlie")
    rows = pg.execute(
        "select endring, arsak from lager.bevegelse where flytt_id = %s order by endring",
        (uuid.UUID(flytt_id),),
    ).fetchall()
    assert rows == [(-5, "flytt"), (5, "flytt")]
    stock = {r.plass: r.antall for r in inv.stock("NO", "MOT-NEMA17", None)}
    assert stock == {"R3-H2": 7, "R4-H2": 5}


def test_no_transfer_between_norway_and_switzerland(pg: psycopg.Connection) -> None:
    flytt = uuid.uuid4()
    with pytest.raises(psycopg.errors.RaiseException, match="Norge og Sveits"), pg.transaction():
        pg.execute(
            "insert into lager.bevegelse (artikkel_id, lagerplass_id, endring, arsak, flytt_id, utfort_av) "
            "select a.id, p.id, x.e, 'flytt', %s, 'test' from lager.artikkel a, "
            "(values ('NO','R1-H1',-1), ('CH','R1-H1',1)) as x(lok, kode, e) "
            "join lager.lagerplass p on p.lokasjon_id = x.lok and p.kode = x.kode "
            "where a.sku = 'MEK-PROF-2020'",
            (flytt,),
        )
    # The repository refuses too: the destination place is looked up in the SAME location.
    assert not hasattr(PgInventory, "transfer")


def test_half_a_move_is_rejected(pg: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.RaiseException, match="ugyldig flytt"), pg.transaction():
        pg.execute(
            "insert into lager.bevegelse (artikkel_id, lagerplass_id, endring, arsak, flytt_id, utfort_av) "
            "select a.id, p.id, -1, 'flytt', %s, 'test' from lager.artikkel a "
            "join lager.lagerplass p on p.lokasjon_id = 'NO' and p.kode = 'R1-H1' "
            "where a.sku = 'MEK-PROF-2020'",
            (uuid.uuid4(),),
        )


def test_esd_article_cannot_leave_esd_zone(executor_pool: ConnectionPool) -> None:
    inv = PgInventory(executor_pool)
    with pytest.raises(psycopg.errors.RaiseException, match="ESD"):
        inv.move("MCU-ESP32S3", "NO", "ESD-2", "R5-H1", 1, "charlie")


def test_stock_never_negative(executor_pool: ConnectionPool) -> None:
    inv = PgInventory(executor_pool)
    with pytest.raises(psycopg.errors.CheckViolation):
        inv.move("BAT-LIPO-4S", "NO", "R11-H1", "R10-H1", 99, "charlie")


def test_movements_are_history(pg: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.RaiseException, match="historikk"):
        pg.execute("update lager.bevegelse set endring = 100")
    with pytest.raises(psycopg.errors.RaiseException, match="historikk"):
        pg.execute("delete from lager.bevegelse")


def test_unknown_place_in_location_denied(executor_pool: ConnectionPool) -> None:
    inv = PgInventory(executor_pool)
    with pytest.raises(Denied):
        inv.move("MOT-NEMA17", "NO", "R12-H1", "R4-H2", 1, "charlie")  # no shelf R12
    with pytest.raises(psycopg.errors.RaiseException, match="negativ"):
        inv.move("MOT-NEMA17", "CH", "R3-H2", "R4-H2", 1, "charlie")  # none of it in CH


def test_charlie_moves_on_level2_and_orders_on_level3_with_db_prices(
    executor_pool: ConnectionPool,
) -> None:
    sim = SimSystem(inventory=PgInventory(executor_pool))
    owner = sim.cfg.identity.owner_numbers[0]
    said = sim.call("charlie", owner, ["Flytt 2 SEN-ENC-AS5600 fra ESD-4 til ESD-3"])
    assert "Gjort: inventory.move (nivå 2)" in said[-1]
    from assistants.sim import DEMO_PIN  # noqa: PLC0415

    said = sim.call("charlie", owner, ["Bestill 10 DRV-TMC2209 fra elfa"], pin=DEMO_PIN)
    assert "nivå 3" in said[-1]
    [pending] = sim.proposals.list_status("pending")
    assert pending.amount_nok == 790.0
    assert sim.approve_on_phone(str(pending.id)).status == "executed"
    assert sim.supplier.orders[0]["total_nok"] == 790.0
