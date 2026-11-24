"""Migration 0032 (phase 7.13, T1a): the chain of drafts.

- proposal_bases: which upstream drafts a draft was built on. Same feed, an earlier kind, an agent's draft; append-only.
- An approval of a draft is refused while any of its bases is not approved (rule 1: top to bottom).
- onboarding_sessions.run_mode: step or chain, and only step -> chain.
- The chain events are known event names.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from core.domain.mapping import ElementOption
from core.repositories.onboarding import OnboardingRepository
from core.repositories.proposals import ProposalRepository
from tests.configured import (
    a_registered_feed,
    glossary_proposal,
    mapping_proposal,
    new_element,
    rule_proposal,
    transformation_proposal,
)
from tests.conftest import dev_user_for
from tests.test_db_onboarding_guards import refused

BASE = "INSERT INTO proposal_bases (proposal_id, base_id, feed_id) VALUES (:p, :b, :f)"


@pytest.fixture
def s(db_session, glossary):
    return db_session


@pytest.fixture
def feed(s):
    return a_registered_feed(s, "syn_chain")


def element(key: str) -> ElementOption:
    return new_element(key, name=key.replace("_", " ").title())


def chain(s, feed):
    """A glossary draft, a mapping draft on it, a transformation draft on the mapping, a rule draft on both."""
    g = glossary_proposal(s, [element("syn_chain_px")], feed_id=feed, column="PX")
    m = mapping_proposal(s, feed, "PX", ["syn_chain_px"])
    t = transformation_proposal(s, feed, "PX", [{"op": "trim"}])
    r = rule_proposal(s, feed, "syn_chain_px_present")
    bases = ProposalRepository(s)
    bases.add_bases(m.id, [g.id], feed_id=feed)
    bases.add_bases(t.id, [m.id], feed_id=feed)
    bases.add_bases(r.id, [m.id, t.id], feed_id=feed)
    s.flush()
    return g, m, t, r


def test_a_draft_records_the_drafts_it_was_built_on(s, feed):
    g, m, t, r = chain(s, feed)
    repo = ProposalRepository(s)
    assert repo.bases(m.id) == [g.id]
    assert set(repo.bases(r.id)) == {m.id, t.id}
    assert repo.bases(g.id) == []


def test_a_base_is_of_the_same_feed_an_earlier_kind_and_not_the_draft_itself(s, feed):
    g, m, t, r = chain(s, feed)
    other = a_registered_feed(s, "syn_chain_other")
    refused(s, "same feed", BASE, p=r.id, b=m.id, f=other)
    refused(s, "earlier", BASE, p=g.id, b=m.id, f=feed)  # a glossary draft is built on nothing later
    refused(s, "earlier", BASE, p=m.id, b=r.id, f=feed)
    refused(s, "itself|earlier", BASE, p=m.id, b=m.id, f=feed)


def test_a_person_made_item_has_no_bases(s, feed):
    g = glossary_proposal(s, [element("syn_chain_qty")], feed_id=feed, column="QTY")
    manual = s.execute(text(
        "INSERT INTO proposals (kind, payload, autonomy, required_approver_roles, feed_id, origin, author) "
        "VALUES ('mapping', CAST(:payload AS jsonb), 'A1', ARRAY['data_steward'], :feed, 'manual', 'dev-operator') "
        "RETURNING id"), {"payload": json.dumps({"source_field": "QTY", "column_type": None,
                                                  "candidates": ["syn_chain_qty"], "recommended": "syn_chain_qty",
                                                  "reason": "Synthetic."}), "feed": feed}).scalar_one()
    refused(s, "agent", BASE, p=manual, b=g.id, f=feed)


APPROVE = ("INSERT INTO approvals (proposal_id, step, role, decided_by, decision) "
           "VALUES (:p, 1, 'data_steward', 'dev-data-steward', :d)")


def test_a_draft_is_approved_only_after_every_draft_it_was_built_on(s, feed):
    """The database itself (the code refuses first with UpstreamNotApproved: tests/test_chain_review.py)."""
    g, m, t, r = chain(s, feed)
    repo, steward = ProposalRepository(s), dev_user_for("data_steward")
    refused(s, "not approved", APPROVE, p=m.id, d="approved")
    repo.record_decision(g.id, user=dev_user_for("glossary_owner"), decision="approved")
    repo.record_decision(m.id, user=steward, decision="approved")
    refused(s, "not approved", APPROVE, p=r.id, d="approved")  # the rule's transformation is still pending
    repo.record_decision(t.id, user=steward, decision="approved")
    repo.record_decision(r.id, user=steward, decision="approved")
    s.flush()


def test_a_draft_on_a_rejected_draft_can_be_rejected_but_never_approved(s, feed):
    g, m, t, r = chain(s, feed)
    repo = ProposalRepository(s)
    repo.record_decision(g.id, user=dev_user_for("glossary_owner"), decision="rejected", note="not an element")
    refused(s, "not approved", APPROVE, p=m.id, d="approved")
    repo.record_decision(m.id, user=dev_user_for("data_steward"), decision="rejected", note="its element is gone")
    s.flush()


def test_proposal_bases_is_append_only(s, feed):
    g, m, _, _ = chain(s, feed)
    refused(s, "append-only", "UPDATE proposal_bases SET base_id = base_id WHERE proposal_id = :p", p=m.id)
    refused(s, "append-only", "DELETE FROM proposal_bases WHERE proposal_id = :p", p=m.id)
    refused(s, "append-only|TRUNCATE", "TRUNCATE proposal_bases")


def test_deleting_the_feed_deletes_its_bases(s, feed):
    chain(s, feed)
    s.execute(text("SELECT amaze_delete_feed(:f, 'dev-operator', 'Synthetic test feed.')"), {"f": feed})
    assert s.scalar(text("SELECT count(*) FROM proposal_bases WHERE feed_id = :f"), {"f": feed}) == 0


def test_a_session_runs_step_by_step_until_it_is_set_to_run_the_chain_and_never_back(s, feed):
    sessions = OnboardingRepository(s)
    created = sessions.create(feed_id=feed, created_by="dev-operator")
    assert created.run_mode == "step"
    sessions.set_run_mode(created.id, "chain")
    s.flush()
    assert sessions.get(created.id).run_mode == "chain"
    refused(s, "run_mode", "UPDATE onboarding_sessions SET run_mode = 'step' WHERE id = :i", i=created.id)
    refused(s, "run_mode", "UPDATE onboarding_sessions SET run_mode = 'later' WHERE id = :i", i=created.id)


@pytest.mark.parametrize("event", ["chain_started", "chain_done", "chain_stopped", "draft_stale"])
def test_the_chain_events_are_known(s, feed, event):
    created = OnboardingRepository(s).create(feed_id=feed, created_by="dev-operator")
    OnboardingRepository(s).add_event(created.id, step="mapping", event=event, actor="system")
    s.flush()
