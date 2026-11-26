"""Phase 7.22, T3: Request glossary element (docs/plans/phase-7.22.md, decisions D4 to D6 and section 5).

- Request queues a glossary run for that column only (reason column_requested); the builder asks about nothing else.
- Approving the requested element creates that column's mapping proposal: manual, authored by the person who pressed
  Request, no model call, pending for the steward. In the Glossary requests step the operator's one click approves both.
- The step never blocks: an element the person cannot approve stays pending, "waiting for glossary (not loaded yet)", and
  the onboarding goes on to go-live without the column.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from core.repositories.configuration import MappingRepository
from core.repositories.errors import NotACannotMapColumn
from core.repositories.glossary_runs import GlossaryRunRepository
from core.repositories.go_lives import GoLiveRepository
from core.repositories.proposals import ProposalRepository
from core.steps.completion import completion_report
from core.steps.onboarding import approve_and_next, decide_cannot_map
from core.steps.s05_glossary import decide_element
from core.steps.s06_map import decide_mapping, excluded_columns
from ports.notify.inapp import InAppNotifier
from tests.test_onboarding import IDENTITY, OPERATOR, approve, current, events, glossary_has_them, make_world, run_to, view, work

REQUESTER = IDENTITY.get_user("dev-backend-team")  # a backend person without the glossary_owner or data_steward role


def at_mapping(db_session, tmp_path, **kw):
    w = make_world(db_session, tmp_path, **kw)
    work(w)
    approve(w)
    work(w)
    return w


def settle_mapping(w, request: list[str], by=OPERATOR, ignore: list[str] = ()) -> list[str]:
    """Decide the mapping step: the ties, a request for `request` (as `by`), Ignore for `ignore`, and the test scaffolding
    (an approved element and mapping) for every other column no element fits."""
    for item in [i for i in view(w).items if i.needs_decision]:
        decide_mapping(w.session, proposal_id=item.proposal_id, user=OPERATOR, decision="approved",
                       choice=item.payload.get("recommended") or item.payload["candidates"][0], note=None)
    rest = [c.column for c in view(w).cannot_map
            if c.state == "undecided" and c.column not in request and c.column not in ignore]
    glossary_has_them(w, rest)
    decide_cannot_map(w.session, session_id=w.session_id, action="request", columns=request, user=by)
    if ignore:
        decide_cannot_map(w.session, session_id=w.session_id, action="ignore", columns=list(ignore), user=OPERATOR)
    w.session.commit()
    return request


def pick(w, n: int = 1) -> list[str]:
    return [c.column for c in view(w).cannot_map][:n]


def run_rows(s, feed_id):
    return GlossaryRunRepository(s).list_for_feed(feed_id)


def requested_proposals(s, feed_id, status: str | None = None):
    """The glossary proposals the builder made for a requested run (the test scaffolding made others, with no such run)."""
    runs = {r.id for r in run_rows(s, feed_id) if r.reason == "column_requested"}
    return [p for p in ProposalRepository(s).list_for_feed(feed_id, "glossary_element", status)
            if p.payload.get("run_id") and p.payload["run_id"] in {str(r) for r in runs}]


def test_request_queues_a_run_for_that_column_only_and_marks_it_requested(db_session, glossary, tmp_path):
    w = at_mapping(db_session, tmp_path)
    column = pick(w)[0]
    decided = decide_cannot_map(db_session, session_id=w.session_id, action="request", columns=[column], user=REQUESTER)
    db_session.commit()
    [run] = run_rows(db_session, w.feed_id)
    assert (run.reason, run.columns, run.requested_by, run.status) == ("column_requested", [column], REQUESTER.id, "queued")
    assert decided.proposals == [] and decided.run_id == run.id
    assert {c.column: c.state for c in view(w).cannot_map}[column] == "requested"
    assert [e.actor for e in events(w, "glossary_requested")] == [REQUESTER.id]
    with pytest.raises(NotACannotMapColumn):  # decided: not asked twice
        decide_cannot_map(db_session, session_id=w.session_id, action="request", columns=[column], user=REQUESTER)
    db_session.rollback()


def test_the_builder_is_asked_about_the_requested_column_and_nothing_else(db_session, glossary, tmp_path):
    w = at_mapping(db_session, tmp_path)
    column = pick(w)[0]
    settle_mapping(w, [column], by=REQUESTER)
    approve(w)  # on to the Glossary requests step
    assert current(w).current_step == "glossary"
    work(w)  # its job runs the requested run
    assert [p.payload["source_field"] for p in requested_proposals(db_session, w.feed_id)] == [column]
    [run] = [r for r in run_rows(db_session, w.feed_id) if r.reason == "column_requested"]
    assert run.status == "done" and set(run.results) == {column}, "only the requested column was looked at"
    assert [i.column for i in view(w).items] == [column]


def test_approving_the_element_creates_the_requesters_mapping_proposal_and_one_click_maps_the_column(db_session, glossary,
                                                                                                   tmp_path):
    w = at_mapping(db_session, tmp_path)
    column = pick(w)[0]
    settle_mapping(w, [column], by=REQUESTER)
    approve(w)
    work(w)
    calls = db_session.scalar(text("SELECT count(*) FROM proposal_log"))

    approve(w)  # the operator holds glossary_owner and data_steward: the element, then its mapping

    assert current(w).current_step == "transformations"
    [element] = requested_proposals(db_session, w.feed_id)
    assert element.status == "approved"
    mapped = {m.source_field: m for m in MappingRepository(db_session).current_for_feed(w.feed_id)}
    assert column in mapped
    mapping = ProposalRepository(db_session).get(mapped[column].proposal_id)
    assert (mapping.origin, mapping.author, mapping.proposal_log_id, mapping.status) == ("manual", REQUESTER.id, None, "approved")
    assert db_session.scalar(text("SELECT count(*) FROM proposal_log")) == calls, "no model call for the mapping"
    assert not any(c.column == column for c in view(w).cannot_map)


def test_an_element_nobody_present_may_approve_stays_pending_and_the_step_goes_on(db_session, glossary, tmp_path):
    w = at_mapping(db_session, tmp_path)
    column = pick(w)[0]
    settle_mapping(w, [column], by=REQUESTER)
    approve(w)
    work(w)

    advance = approve_and_next(db_session, session_id=w.session_id, user=REQUESTER, ctx=w.ctx)  # no glossary_owner role
    db_session.commit()

    assert advance.moved and current(w).current_step == "transformations"
    [element] = requested_proposals(db_session, w.feed_id)
    assert element.status == "pending"
    assert column not in {m.source_field for m in MappingRepository(db_session).current_for_feed(w.feed_id)}
    report = completion_report(db_session, w.feed_id)
    assert report.mappings.waiting_for_glossary == [column] and report.mappings.undecided == 0


def test_go_live_goes_ahead_with_a_requested_column_pending_and_the_report_names_it(db_session, glossary, tmp_path):
    w = at_mapping(db_session, tmp_path)
    requested, ignored = pick(w, 2)
    settle_mapping(w, [requested], by=REQUESTER, ignore=[ignored])
    approve(w)
    work(w)
    approve_and_next(db_session, session_id=w.session_id, user=REQUESTER, ctx=w.ctx)  # the element stays pending
    db_session.commit()

    run_to(w, "go_live")
    report = completion_report(db_session, w.feed_id)

    assert report.mappings.waiting_for_glossary == [requested] and ignored in report.mappings.excluded_columns
    assert report.ready_for_go_live, "an ignored or requested column never blocks go-live"
    assert any(requested in line and "waiting for glossary (not loaded yet)" in line for line in report.words)
    approve(w)  # the owner approves go-live
    assert current(w).current_step == "first_load"
    assert requested not in {m.source_field for m in MappingRepository(db_session).current_for_feed(w.feed_id)}


def test_the_feed_page_and_the_product_page_name_the_ignored_and_the_waiting_columns(db_session, glossary, tmp_path):
    from core.repositories.feeds import FeedRepository
    from core.repositories.products import ProductRepository
    from core.steps.feed_page import feed_page
    from core.steps.product_page import product_page
    from ports.connectors.catalogue import SourceCatalogue
    from core.repositories.upload_sources import UploadSourceRepository

    w = at_mapping(db_session, tmp_path)
    requested, ignored = pick(w, 2)
    settle_mapping(w, [requested], by=REQUESTER, ignore=[ignored])
    approve(w)
    work(w)
    approve_and_next(db_session, session_id=w.session_id, user=REQUESTER, ctx=w.ctx)
    db_session.commit()
    run_to(w, "go_live")
    approve(w)  # go-live
    work(w)  # the first load: released, the product exists

    catalogue = SourceCatalogue.load(w.settings.source_catalogue, w.settings.allowed_source_hosts()).with_uploads(
        UploadSourceRepository(db_session).entries())
    page = feed_page(db_session, FeedRepository(db_session).get(w.feed_id), catalogue)
    # (a profile action that leaves a column out, if the agent proposed one, is listed with the ignored ones)
    assert ignored in page.not_loaded.ignored and page.not_loaded.waiting_for_glossary == [requested]
    product = ProductRepository(db_session).for_feed(w.feed_id)
    shown = product_page(db_session, code=product.code, user=OPERATOR, identity=IDENTITY)
    assert ignored in shown.not_loaded.ignored and shown.not_loaded.waiting_for_glossary == [requested]
    assert shown.not_loaded == page.not_loaded


def test_approving_the_element_later_creates_the_proposal_after_go_live_and_the_load_waits_for_a_new_go_live(db_session,
                                                                                                        glossary,
                                                                                                        tmp_path):
    w = at_mapping(db_session, tmp_path)
    requested = pick(w)[0]
    settle_mapping(w, [requested], by=REQUESTER)
    approve(w)
    work(w)
    approve_and_next(db_session, session_id=w.session_id, user=REQUESTER, ctx=w.ctx)
    db_session.commit()
    run_to(w, "go_live")
    approve(w)  # go-live
    live = GoLiveRepository(db_session).latest(w.feed_id)

    [element] = requested_proposals(db_session, w.feed_id, "pending")
    done = decide_element(db_session, proposal_id=element.id, user=OPERATOR, decision="approved", choice=None, note=None,
                          notifier=InAppNotifier(db_session), stewards=[])
    assert done.mapping_proposal_id is not None
    mapping = ProposalRepository(db_session).get(done.mapping_proposal_id)
    assert (mapping.status, mapping.origin, mapping.author) == ("pending", "manual", REQUESTER.id)
    decide_mapping(db_session, proposal_id=mapping.id, user=OPERATOR, decision="approved", choice=None, note=None)
    db_session.commit()

    now = {m.id for m in MappingRepository(db_session).current_for_feed(w.feed_id)}
    assert now - set(live.mapping_ids), "the new mapping is not in the go-live: the engine refuses loads until a new one"


def test_an_element_that_was_not_requested_creates_no_mapping_proposal(db_session, glossary, tmp_path):
    from tests.configured import glossary_proposal, new_element

    w = at_mapping(db_session, tmp_path)
    option = new_element("other_element", data_type="string", domain="reference_data")
    p = glossary_proposal(db_session, [option], feed_id=w.feed_id, column=pick(w)[0])
    done = decide_element(db_session, proposal_id=p.id, user=OPERATOR, decision="approved", choice=None, note=None,
                          notifier=InAppNotifier(db_session), stewards=[])
    assert done.mapping_proposal_id is None, "only a requested column's approval makes one (the workbench path is as before)"


def test_a_rejected_ignore_brings_the_column_back_and_a_requested_one_is_not_ignored_too(db_session, glossary, tmp_path):
    w = at_mapping(db_session, tmp_path)
    a, b = pick(w, 2)
    decide_cannot_map(db_session, session_id=w.session_id, action="request", columns=[a], user=REQUESTER)
    with pytest.raises(NotACannotMapColumn):
        decide_cannot_map(db_session, session_id=w.session_id, action="ignore", columns=[a], user=OPERATOR)
    db_session.rollback()
    decide_cannot_map(db_session, session_id=w.session_id, action="ignore", columns=[b], user=OPERATOR)
    assert b in excluded_columns(db_session, w.feed_id) or {c.column: c.state for c in view(w).cannot_map}[b] == "ignored"
