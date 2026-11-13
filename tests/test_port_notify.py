"""Notifier port, local adapter: in-app notifications stored in the notifications table."""

import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from ports.notify.inapp import InAppNotifier, NotificationRow


def test_notify_stores_a_row_and_returns_its_id(db_session):
    notification_id = InAppNotifier(db_session).notify(
        recipient="dev-requester",
        subject="Ready",
        body="Your data product is ready.",
        link="/requests/1",
    )
    row = db_session.get(NotificationRow, uuid.UUID(notification_id))
    assert (row.recipient, row.subject, row.body, row.link) == (
        "dev-requester", "Ready", "Your data product is ready.", "/requests/1",
    )
    assert row.created_at is not None
    assert row.read_at is None


def test_link_is_optional(db_session):
    notification_id = InAppNotifier(db_session).notify(recipient="dev-checker", subject="s", body="b")
    assert db_session.get(NotificationRow, uuid.UUID(notification_id)).link is None


@pytest.mark.parametrize("field", ["recipient", "subject", "body"])
def test_required_fields(db_session, field):
    kwargs = {"recipient": "dev-checker", "subject": "s", "body": "b", field: " "}
    with pytest.raises(ValueError):
        InAppNotifier(db_session).notify(**kwargs)


def test_without_a_session_it_commits_its_own(migrated_engine):
    factory = sessionmaker(bind=migrated_engine)
    notification_id = InAppNotifier(session_factory=factory).notify(recipient="dev-ops-test", subject="s", body="b")
    with factory() as session:
        try:
            assert session.scalars(
                select(NotificationRow).where(NotificationRow.id == uuid.UUID(notification_id))
            ).one().recipient == "dev-ops-test"
        finally:
            session.execute(delete(NotificationRow).where(NotificationRow.recipient == "dev-ops-test"))
            session.commit()
