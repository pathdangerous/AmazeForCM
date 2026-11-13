"""NotificationRepository reads the phase 0 notifications table; writing stays with the Notifier port."""

from core.domain.notification import Notification
from core.repositories.notifications import NotificationRepository
from ports.notify.inapp import InAppNotifier


def test_list_for_recipient_newest_first(db_session):
    notifier = InAppNotifier(db_session)
    ids = [notifier.notify(recipient="dev-requester", subject=f"S{n}", body="Synthetic.") for n in range(3)]
    notifier.notify(recipient="dev-data-owner", subject="Other", body="Synthetic.")
    listed = NotificationRepository(db_session).list_for("dev-requester")
    assert all(isinstance(n, Notification) for n in listed)
    assert [str(n.id) for n in listed] == list(reversed(ids))
    assert NotificationRepository(db_session).list_for("dev-nobody") == []
