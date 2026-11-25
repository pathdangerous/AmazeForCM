"""The consumer's product API (phase 7.8, task 7.1; plan assumption 43): the catalogue by domain, the product
page, the preview of the first 100 rows, and the CSV and Excel downloads. GETs only, no model, no write.

A product is made the way production makes it: a scheduled load through steps 12, 13, 15 and 20 (tests/step_chains).
"""

import csv
import io
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from core.repositories.grants import ProductGrantRepository
from core.repositories.notifications import NotificationRepository
from core.repositories.products import ProductRepository
from core.steps.s20_create_product import FinishContext
from ports.config import Settings
from ports.notify.inapp import InAppNotifier
from ports.storage.local import LocalBlobStore
from tests.configured import approved_transformation, go_live, loadable_feed, volume_csv
from tests.conftest import running_app
from tests.step_chains import run_scheduled

pytestmark = pytest.mark.anyio

RILEY = {"X-Dev-User": "dev-requester"}
OWNER = {"X-Dev-User": "dev-data-owner"}
BASE = "/api/consumer"


def deliver(world, content: str, feed=None):
    feed = feed or world.feed
    world.n += 1
    key = f"raw/{world.n}/f.csv"
    world.blobs.put(key, content.encode())
    return run_scheduled(world.session, world.blobs, feed.id, feed.live.id, file_ref=key, context=world.context)


def make_feed(world, code, **kw):
    feed_id, live = loadable_feed(world.session, code=code, **kw)
    return SimpleNamespace(id=feed_id, live=live, code=code)


@pytest.fixture
def world(db_session, glossary, tmp_path):
    context = FinishContext(notifier=InAppNotifier(db_session), threshold=Decimal("90"), operations=["dev-operations"],
                            backend_team=["dev-backend-team"], roles_of=lambda uid: ["requester"],
                            display_name=lambda uid: uid)
    world = SimpleNamespace(session=db_session, blobs=LocalBlobStore(tmp_path / "blob"), context=context, n=0)
    world.feed = make_feed(world, "syn_loadable")
    return world


@pytest.fixture
async def client(world, migrated_engine):
    world.session.commit()
    async with running_app(engine=migrated_engine, settings=Settings(_env_file=None), session=world.session) as c:
        yield c


@pytest.fixture
def released(world):
    """The feed's product with 150 released records."""
    assert deliver(world, volume_csv(150, 0)).run.load.status == "released"
    return world


def rows_of(response):
    return list(csv.reader(io.StringIO(response.text)))


async def test_the_catalogue_groups_a_product_under_its_domain(released, client):
    response = await client.get(f"{BASE}/catalogue", headers=RILEY)
    assert response.status_code == 200, response.text
    groups = {g["domain"]: g for g in response.json()}
    (product,) = groups["pricing"]["products"]
    assert groups["pricing"]["label"] == "Pricing"
    assert (product["code"], product["access"], product["route"], product["visibility"]) == (
        "syn_loadable", "entitled", None, "open")
    assert product["owner_name"] == "Devon Owner" and product["description"].startswith("Released from")
    assert product["freshness"]["rows"] == 150 and product["freshness"]["released_at"]


async def test_the_page_has_owner_freshness_and_the_dictionary(released, client):
    response = await client.get(f"{BASE}/products/syn_loadable", headers=RILEY)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["owner"] == {"id": "dev-data-owner", "name": "Devon Owner"}
    assert page["domain"] == "pricing" and page["visibility"] == "open" and page["access"] == "entitled"
    assert page["description"] and page["freshness"]["rows"] == 150
    assert page["columns"] == ["instrument_id", "price_date", "volume"]  # the feed's mappings, by source field
    by_key = {d["element_key"]: d for d in page["dictionary"]}
    assert [d["element_key"] for d in page["dictionary"]] == page["columns"]
    assert by_key["volume"]["name"] == "Volume" and by_key["volume"]["data_type"] == "integer"
    assert by_key["volume"]["definition"] and by_key["volume"]["label"] == "Volume"
    assert "unit" in by_key["volume"]


async def test_the_preview_is_the_first_100_rows_in_row_order_with_labels(released, client):
    response = await client.get(f"{BASE}/products/syn_loadable/preview", headers=RILEY)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["columns"] == ["Instrument ID", "Price date", "Volume"]
    assert len(preview["rows"]) == 100
    assert preview["rows"][0] == ["syn-0001", "2026-11-02", 1000]
    assert preview["rows"][99][0] == "syn-0100"


async def test_product_has_real_certification_and_completed_stage_lineage(released, client):
    page = (await client.get(f"{BASE}/products/syn_loadable", headers=RILEY)).json()
    assert page["certification"] == {"trust_score": 100.0, "rows": 150, "flagged_rows": 0}
    assert page["lineage"]
    assert page["lineage"][-1] == {"stage": "product", "name": page["name"], "rows": 150}
    assert any(stage["stage"] == "standard" and stage["rows"] == 150 for stage in page["lineage"])
    assert all(stage["rows"] is None or stage["rows"] >= 0 for stage in page["lineage"])


async def test_a_small_product_previews_all_its_rows(world, client):
    deliver(world, volume_csv(7, 0))
    preview = (await client.get(f"{BASE}/products/syn_loadable/preview", headers=RILEY)).json()
    assert [r[0] for r in preview["rows"]] == [f"syn-{n:04d}" for n in range(1, 8)]


async def test_the_csv_download_has_every_row_and_parses_back(released, client):
    response = await client.get(f"{BASE}/products/syn_loadable/download.csv", headers=RILEY)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="syn_loadable.csv"'
    assert not response.content.startswith(b"\xef\xbb\xbf")  # plain UTF-8
    parsed = rows_of(response)
    assert parsed[0] == ["Instrument ID", "Price date", "Volume"] and len(parsed) == 151
    assert parsed[1] == ["syn-0001", "2026-11-02", "1000"] and parsed[150][0] == "syn-0150"


async def test_the_xlsx_download_has_a_bold_header_and_typed_cells(released, client):
    response = await client.get(f"{BASE}/products/syn_loadable/download.xlsx", headers=RILEY)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["content-disposition"] == 'attachment; filename="syn_loadable.xlsx"'
    sheet = load_workbook(io.BytesIO(response.content)).active
    assert sheet.title == "syn_loadable"
    header, first = list(sheet.iter_rows(min_row=1, max_row=2))
    assert [c.value for c in header] == ["Instrument ID", "Price date", "Volume"] and all(c.font.bold for c in header)
    assert first[0].value == "syn-0001"
    assert isinstance(first[1].value, datetime) and first[1].value.date().isoformat() == "2026-11-02"
    assert isinstance(first[2].value, int) and first[2].value == 1000
    assert sheet.max_row == 151


# ---------------------------------------------------------------- column order (fix-column-order)

ORDERED_HEADER = "traded_qty,instr_id,trade_date\n"  # the file: volume, instrument_id, price_date (alphabetically not)
FILE_ORDER = ["Volume", "Instrument ID", "Price date"]


def _column(name: str):
    from core.domain.profiling import FileColumn

    return FileColumn(name=name, type="VARCHAR", null_count=0, null_rate=0.0, distinct_count=1)


def ordered_feed(world, code="syn_ordered"):
    """A feed like loadable_feed, but whose profile (the template) lists the file's own column order."""
    from core.repositories.feeds import FeedRepository
    from tests.configured import VOLUME_RULE, approved_mapping, approved_rule
    from tests.onboarded import registered_feed

    feed = FeedRepository(world.session).create_candidate(code=code, name="Syn ordered", source_code=code,
                                                          connector="local_file", source_ref=f"files/{code}.csv")
    registered_feed(world.session, feed.id, columns=[_column(n) for n in ("traded_qty", "instr_id", "trade_date")])
    for field, key in (("instr_id", "instrument_id"), ("trade_date", "price_date"), ("traded_qty", "volume")):
        approved_mapping(world.session, feed.id, field, key)
    approved_rule(world.session, feed.id, VOLUME_RULE, sql="SELECT _row FROM curated WHERE volume > 100000",
                  description="Volume is at most 100000.")
    return SimpleNamespace(id=feed.id, live=go_live(world.session, feed.id), code=code)


def ordered_csv(rows: int = 5) -> str:
    return ORDERED_HEADER + "".join(f"1000,syn-{n:04d},2026-11-02\n" for n in range(1, rows + 1))


async def test_every_view_follows_the_files_column_order(world, client):
    feed = ordered_feed(world)
    assert deliver(world, ordered_csv(), feed).run.load.status == "released"
    page = (await client.get(f"{BASE}/products/syn_ordered", headers=RILEY)).json()
    assert page["columns"] == ["volume", "instrument_id", "price_date"]
    assert [d["element_key"] for d in page["dictionary"]] == page["columns"]
    preview = (await client.get(f"{BASE}/products/syn_ordered/preview", headers=RILEY)).json()
    assert preview["columns"] == FILE_ORDER and preview["rows"][0] == [1000, "syn-0001", "2026-11-02"]
    csv_rows = rows_of(await client.get(f"{BASE}/products/syn_ordered/download.csv", headers=RILEY))
    assert csv_rows[0] == FILE_ORDER and csv_rows[1] == ["1000", "syn-0001", "2026-11-02"]
    xlsx = await client.get(f"{BASE}/products/syn_ordered/download.xlsx", headers=RILEY)
    first = [c.value for c in next(load_workbook(io.BytesIO(xlsx.content)).active.iter_rows(min_row=1, max_row=1))]
    assert first == FILE_ORDER


async def test_a_column_added_later_appears_where_the_new_profile_puts_it(world, client):
    from core.repositories.tables import ProductElementRow
    from tests.configured import approved_mapping
    from tests.onboarded import profiled

    feed = ordered_feed(world)
    assert deliver(world, ordered_csv(), feed).run.load.status == "released"
    product = ProductRepository(world.session).get_by_code("syn_ordered")
    world.session.add(ProductElementRow(product_id=product.id, element_key="close_price"))
    approved_mapping(world.session, feed.id, "px_close", "close_price")
    profiled(world.session, feed.id, columns=[_column(n) for n in ("traded_qty", "px_close", "instr_id", "trade_date")])
    world.session.commit()
    page = (await client.get(f"{BASE}/products/syn_ordered", headers=RILEY)).json()
    assert page["columns"] == ["volume", "close_price", "instrument_id", "price_date"]


async def test_a_product_without_a_feed_keeps_its_stored_order(world, client, make_product):
    make_product(code="syn_nofeed", element_keys=("volume", "close_price", "instrument_id"))
    world.session.commit()
    page = (await client.get(f"{BASE}/products/syn_nofeed", headers=RILEY)).json()
    assert page["columns"] == ["close_price", "instrument_id", "volume"]


async def test_an_element_no_mapping_names_is_left_out_of_the_page(world, client):
    """Phase 7.19 (was: goes last alphabetically): an element of a feed's product that the feed's current mappings no
    longer name stays in product_elements but is not shown."""
    from core.repositories.tables import ProductElementRow

    feed = ordered_feed(world)
    deliver(world, ordered_csv(), feed)
    product = ProductRepository(world.session).get_by_code("syn_ordered")
    world.session.add(ProductElementRow(product_id=product.id, element_key="close_price"))  # no column maps to it
    world.session.commit()
    page = (await client.get(f"{BASE}/products/syn_ordered", headers=RILEY)).json()
    assert page["columns"] == ["volume", "instrument_id", "price_date"]
    assert "close_price" in ProductRepository(world.session).elements_of(product.id)


# ---------------------------------------------------------------- phase 7.19: a column added after the first release

NEW_COLUMN_ORDER = ["traded_qty", "px_close", "instr_id", "trade_date"]  # the new file: volume, close_price, ...


def new_csv(rows: int = 3, *, volume: int = 1000, column: str = "px_close") -> str:
    return (f"traded_qty,{column},instr_id,trade_date\n"
            + "".join(f"{volume},12.5,syn-{n:04d},2026-11-02\n" for n in range(1, rows + 1)))


def add_column(world, feed, element="close_price", column="px_close"):
    """Version 2 of the feed's code: the new column mapped, the new file's profile, a go-live pinning it."""
    from tests.configured import approved_mapping
    from tests.onboarded import profiled

    approved_mapping(world.session, feed.id, column, element)
    names = ["traded_qty", column, "instr_id", "trade_date"]
    profiled(world.session, feed.id, columns=[_column(n) for n in names])
    feed.live = go_live(world.session, feed.id)


def notices(world, recipient="dev-requester", like="%gained column%"):
    from sqlalchemy import text

    return [r for r in world.session.execute(text(
        "SELECT subject, body, link FROM notifications WHERE recipient = :r AND subject LIKE :l ORDER BY created_at"),
        {"r": recipient, "l": like})]


async def test_after_version_2_releases_every_view_has_the_new_column_in_the_files_position(world, client):
    feed = ordered_feed(world)
    assert deliver(world, ordered_csv(), feed).run.load.status == "released"
    add_column(world, feed)
    finished = deliver(world, new_csv(), feed)
    assert finished.run.load.status == "released"
    world.session.commit()
    product = ProductRepository(world.session).get_by_code("syn_ordered")
    assert ProductRepository(world.session).elements_of(product.id) == [
        "close_price", "instrument_id", "price_date", "volume"]  # added; nothing deleted
    page = (await client.get(f"{BASE}/products/syn_ordered", headers=RILEY)).json()
    assert page["columns"] == ["volume", "close_price", "instrument_id", "price_date"]
    assert [d["element_key"] for d in page["dictionary"]] == page["columns"]
    labels = ["Volume", "Close price", "Instrument ID", "Price date"]
    preview = (await client.get(f"{BASE}/products/syn_ordered/preview", headers=RILEY)).json()
    assert preview["columns"] == labels
    assert [preview["rows"][0][0], float(preview["rows"][0][1]), preview["rows"][0][2]] == [1000, 12.5, "syn-0001"]
    csv_rows = rows_of(await client.get(f"{BASE}/products/syn_ordered/download.csv", headers=RILEY))
    assert csv_rows[0] == labels and csv_rows[1][2:] == ["syn-0001", "2026-11-02"] and float(csv_rows[1][1]) == 12.5
    xlsx = await client.get(f"{BASE}/products/syn_ordered/download.xlsx", headers=RILEY)
    header = [c.value for c in next(load_workbook(io.BytesIO(xlsx.content)).active.iter_rows(min_row=1, max_row=1))]
    assert header == labels


def test_older_loads_show_the_new_column_empty(world):
    from core.repositories.standard_records import StandardRecordRepository

    feed = ordered_feed(world)
    first = deliver(world, ordered_csv(), feed)
    add_column(world, feed)
    deliver(world, new_csv(), feed)
    old = list(StandardRecordRepository(world.session).iter_for_load(first.run.load.id, None))
    assert old and all(r.values.get("close_price") is None for r in old)


def test_consumers_are_told_once_per_new_column_and_a_later_release_says_nothing(world):
    from tests.configured import onboarding_requirement

    feed = ordered_feed(world)
    onboarding_requirement(world.session, feed.id, title="Daily volume", elements=["volume"])
    onboarding_requirement(world.session, feed.id, title="Second request by Riley", elements=["volume"])
    onboarding_requirement(world.session, feed.id, title="Someone else", elements=["volume"], requester="dev-operator")
    deliver(world, ordered_csv(), feed)
    assert notices(world) == []  # the first release creates the product: nothing is "gained"
    add_column(world, feed)
    deliver(world, new_csv(), feed)
    [(subject, body, link)] = notices(world)
    assert subject == "Syn ordered gained column Close price" and "Syn ordered" in body
    assert link == "#/product/syn_ordered"
    assert len(notices(world, "dev-operator")) == 1
    deliver(world, new_csv(4), feed)  # the next release has the same columns
    assert len(notices(world)) == 1 and len(notices(world, "dev-operator")) == 1


def test_a_held_load_adds_nothing_and_tells_nobody(world):
    from tests.configured import onboarding_requirement

    feed = ordered_feed(world)
    onboarding_requirement(world.session, feed.id, elements=["volume"])
    deliver(world, ordered_csv(), feed)
    add_column(world, feed)
    held = deliver(world, new_csv(4, volume=200000), feed)  # every row breaks the volume rule: held
    assert held.run.load.status == "held"
    product = ProductRepository(world.session).get_by_code("syn_ordered")
    assert ProductRepository(world.session).elements_of(product.id) == ["instrument_id", "price_date", "volume"]
    assert notices(world) == []


async def test_an_element_no_longer_mapped_stays_on_the_product_and_leaves_every_view(world, client):
    feed = ordered_feed(world)
    deliver(world, ordered_csv(), feed)
    add_column(world, feed)
    deliver(world, new_csv(), feed)
    add_column(world, feed, element="open_price")  # the same column now names another element: close_price is unmapped
    assert deliver(world, new_csv(), feed).run.load.status == "released"
    world.session.commit()
    product = ProductRepository(world.session).get_by_code("syn_ordered")
    assert ProductRepository(world.session).elements_of(product.id) == [
        "close_price", "instrument_id", "open_price", "price_date", "volume"]  # close_price is not deleted
    page = (await client.get(f"{BASE}/products/syn_ordered", headers=RILEY)).json()
    assert page["columns"] == ["volume", "open_price", "instrument_id", "price_date"]
    assert [d["element_key"] for d in page["dictionary"]] == page["columns"]
    labels = ["Volume", "Open price", "Instrument ID", "Price date"]
    assert (await client.get(f"{BASE}/products/syn_ordered/preview", headers=RILEY)).json()["columns"] == labels
    assert rows_of(await client.get(f"{BASE}/products/syn_ordered/download.csv", headers=RILEY))[0] == labels
    xlsx = await client.get(f"{BASE}/products/syn_ordered/download.xlsx", headers=RILEY)
    assert [c.value for c in next(load_workbook(io.BytesIO(xlsx.content)).active.iter_rows(min_row=1, max_row=1))] == labels


async def test_a_restricted_product_is_visible_but_refused_without_a_grant(world, client):
    other = make_feed(world, "syn_secret", visibility="restricted")
    deliver(world, volume_csv(5, 0), other)
    route = "Ask Devon Owner for access to Syn secret"
    groups = (await client.get(f"{BASE}/catalogue", headers=RILEY)).json()
    (product,) = [p for g in groups for p in g["products"] if p["code"] == "syn_secret"]
    assert (product["access"], product["route"]) == ("not_entitled_visible", route)
    hidden = (await client.get(f"{BASE}/products/syn_secret", headers=RILEY)).json()
    assert hidden["access"] == "not_entitled_visible"
    assert hidden["certification"] is None and hidden["lineage"] == []
    for tail in ("preview", "download.csv", "download.xlsx"):
        refused = await client.get(f"{BASE}/products/syn_secret/{tail}", headers=RILEY)
        assert refused.status_code == 403 and refused.json()["detail"] == {"outcome": "not_entitled", "route": route}
    assert (await client.get(f"{BASE}/products/syn_secret/preview", headers=OWNER)).status_code == 200
    ProductGrantRepository(world.session).record(
        product_id=ProductRepository(world.session).get_by_code("syn_secret").id, subject_type="user",
        subject="dev-requester", action="grant", decided_by="dev-data-owner")
    world.session.commit()
    assert (await client.get(f"{BASE}/products/syn_secret/preview", headers=RILEY)).status_code == 200


async def test_an_unknown_code_is_404(client):
    for tail in ("", "/preview", "/download.csv", "/download.xlsx"):
        assert (await client.get(f"{BASE}/products/syn_nothing{tail}", headers=RILEY)).status_code == 404


async def test_a_product_with_no_released_load_is_no_data(world, client, make_product):
    make_product(code="syn_empty", name="Empty product")
    world.session.commit()
    for tail in ("preview", "download.csv", "download.xlsx"):
        response = await client.get(f"{BASE}/products/syn_empty/{tail}", headers=RILEY)
        assert response.status_code == 404 and response.json()["detail"] == {"outcome": "no_data"}
    page = (await client.get(f"{BASE}/products/syn_empty", headers=RILEY)).json()
    assert page["freshness"] is None
    assert page["certification"] is None and page["lineage"] == []


async def test_a_later_held_load_does_not_hide_the_last_released_one(world, client):
    deliver(world, volume_csv(10, 0))
    assert deliver(world, volume_csv(10, 5)).run.load.status == "held"
    preview = (await client.get(f"{BASE}/products/syn_loadable/preview", headers=RILEY)).json()
    assert len(preview["rows"]) == 10 and preview["rows"][4][2] == 1000  # the held file's rows 1 to 5 are 200000


async def test_an_approved_rename_is_the_column_header(world, client):
    approved_transformation(world.session, world.feed.id, "traded_qty", [{"op": "rename", "to": "traded_volume"}])
    world.feed.live = go_live(world.session, world.feed.id)
    deliver(world, volume_csv(3, 0))
    page = (await client.get(f"{BASE}/products/syn_loadable", headers=RILEY)).json()
    assert {d["element_key"]: d["label"] for d in page["dictionary"]}["volume"] == "traded_volume"
    preview = (await client.get(f"{BASE}/products/syn_loadable/preview", headers=RILEY)).json()
    assert preview["columns"] == ["Instrument ID", "Price date", "traded_volume"]
    parsed = rows_of(await client.get(f"{BASE}/products/syn_loadable/download.csv", headers=RILEY))
    assert parsed[0][2] == "traded_volume"


async def test_the_gets_write_nothing(released, client):
    before = len(NotificationRepository(released.session).list_for("dev-requester"))
    for tail in ("catalogue", "products/syn_loadable", "products/syn_loadable/preview",
                 "products/syn_loadable/download.csv"):
        assert (await client.get(f"{BASE}/{tail}", headers=RILEY)).status_code == 200
    assert len(NotificationRepository(released.session).list_for("dev-requester")) == before


async def test_a_backend_user_may_read_too(released, client):
    assert (await client.get(f"{BASE}/catalogue", headers=OWNER)).status_code == 200
    assert (await client.get(f"{BASE}/catalogue")).status_code == 401
