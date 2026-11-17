"""GET /api/notifications: the signed-in dev user's own notifications, newest 50 (phase 7 plan, assumption 37).

Any signed-in user, backend or requester; nobody sees another's. Read only: no state, no read marks. This is how
the story shows "a person is notified" in both UIs.
"""

import pytest

from ports.config import Settings
from ports.notify.inapp import InAppNotifier
from tests.conftest import running_app

pytestmark = pytest.mark.anyio

RILEY = {"X-Dev-User": "dev-requester"}
OPAL = {"X-Dev-User": "dev-operations"}


@pytest.fixture
async def client(db_session, migrated_engine):
    async with running_app(engine=migrated_engine, settings=Settings(_env_file=None), session=db_session) as c:
        yield c


async def test_a_user_sees_their_own_notifications_newest_first(client, db_session):
    notifier = InAppNotifier(db_session)
    notifier.notify(recipient="dev-requester", subject="Ready: first", body="Your data is in A.")
    notifier.notify(recipient="dev-operations", subject="Delivery held: syn_x", body="Not yours.")
    notifier.notify(recipient="dev-requester", subject="Ready: second", body="Your data is in B.")
    db_session.commit()  # a request ends its read with a rollback, as the other API tests' setup knows
    response = await client.get("/api/notifications", headers=RILEY)
    assert response.status_code == 200
    rows = response.json()
    assert [(r["subject"], r["body"]) for r in rows] == [("Ready: second", "Your data is in B."),
                                                         ("Ready: first", "Your data is in A.")]
    assert set(rows[0]) >= {"id", "created_at", "subject", "body", "link", "read_at"} and "recipient" not in rows[0]
    other = await client.get("/api/notifications", headers=OPAL)
    assert [r["subject"] for r in other.json()] == ["Delivery held: syn_x"]


async def test_only_the_newest_fifty_are_listed(client, db_session):
    notifier = InAppNotifier(db_session)
    for n in range(55):
        notifier.notify(recipient="dev-requester", subject=f"Note {n:02d}", body="Synthetic.")
    db_session.commit()
    rows = (await client.get("/api/notifications", headers=RILEY)).json()
    assert len(rows) == 50 and rows[0]["subject"] == "Note 54" and rows[-1]["subject"] == "Note 05"


async def test_no_notifications_is_an_empty_list_and_signed_out_is_401(client):
    assert (await client.get("/api/notifications", headers=OPAL)).json() == []
    assert (await client.get("/api/notifications")).status_code == 401
