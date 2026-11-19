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


async def test_a_restricted_product_is_visible_but_refused_without_a_grant(world, client):
    other = make_feed(world, "syn_secret", visibility="restricted")
    deliver(world, volume_csv(5, 0), other)
    route = "Ask Devon Owner for access to Syn secret"
    groups = (await client.get(f"{BASE}/catalogue", headers=RILEY)).json()
    (product,) = [p for g in groups for p in g["products"] if p["code"] == "syn_secret"]
    assert (product["access"], product["route"]) == ("not_entitled_visible", route)
    assert (await client.get(f"{BASE}/products/syn_secret", headers=RILEY)).json()["access"] == "not_entitled_visible"
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
