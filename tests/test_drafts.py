"""Phase 7.13, T1b: agents draft on the pending drafts before them (plan, decisions 1 and 2).

On the security master, after the profile is approved and the glossary builder has answered, nothing else is
approved: the mapping agent maps onto draft glossary elements, the transformation agent cleans draft-mapped columns,
and the rule recommender back-tests its rules (in the sandbox) on the draft mappings. Each draft records the drafts it
used, and no `mappings`, `transformations` or `rules` row exists before an approval.
"""

from __future__ import annotations

from sqlalchemy import text

from core.repositories.errors import ConfigurationChangedMeanwhile
from core.repositories.proposals import ProposalRepository
from core.steps.drafts import check_unchanged, draft_config
from core.steps.onboarding import _catalogue, edit_item
from core.steps.s06_map import mapping_input, record_mappings
from core.steps.s09_rules import backtest_for, record_rules, rules_input
from core.steps.s10_transform import propose_transformations, transform_input
from tests.test_onboarding import OPERATOR, approve, make_world, work

import pytest


def configuration_rows(s, feed_id) -> dict[str, int]:
    return {t: s.scalar(text(f"SELECT count(*) FROM {t} WHERE feed_id = :f"), {"f": feed_id})
            for t in ("mappings", "transformations", "rules")}


def glossary_drafted(db_session, tmp_path):
    w = make_world(db_session, tmp_path)
    work(w)
    approve(w)  # the profile: registers the feed and queues the glossary run
    work(w)  # the glossary builder answers; its proposals stay pending
    return w


def draft_mappings(w):
    s = w.session
    draft = draft_config(s, w.feed_id)
    agent_input = mapping_input(s, feed_id=w.feed_id, draft=draft)
    s.commit()
    record_mappings(s, agent=w.ctx.agents.mapper, agent_input=agent_input, user=OPERATOR, draft=draft)
    s.commit()
    return draft


def test_the_mapping_agent_maps_onto_draft_glossary_elements_and_records_them(db_session, glossary, tmp_path):
    w = glossary_drafted(db_session, tmp_path)
    proposals = ProposalRepository(db_session)
    drafts = {p.id: p for p in proposals.list_for_feed(w.feed_id, "glossary_element", "pending")}
    new_keys = {p.payload["options"][p.payload["recommended"]]["key"]: p.id for p in drafts.values()
                if p.payload["recommended"] is not None
                and p.payload["options"][p.payload["recommended"]]["kind"] == "new_element"}
    assert new_keys, "the security master has columns the seed glossary lacks"
    assert configuration_rows(db_session, w.feed_id) == {"mappings": 0, "transformations": 0, "rules": 0}

    draft_mappings(w)

    mapped = proposals.list_for_feed(w.feed_id, "mapping", "pending")
    on_drafts = [m for m in mapped if m.payload["recommended"] in new_keys]
    assert on_drafts, "a mapping names a draft element"
    for m in on_drafts:
        assert new_keys[m.payload["recommended"]] in proposals.bases(m.id)
    drafted = {p.payload["options"][p.payload["recommended"]]["key"]: p.id for p in drafts.values()
               if p.payload["recommended"] is not None}  # new elements, and aliases added to approved ones
    for m in mapped:  # the bases are exactly the glossary drafts among its candidates
        assert set(proposals.bases(m.id)) == {drafted[k] for k in m.payload["candidates"] if k in drafted}
    assert configuration_rows(db_session, w.feed_id) == {"mappings": 0, "transformations": 0, "rules": 0}


def test_transformations_and_rules_are_drafted_on_draft_mappings_with_no_configuration_row(db_session, glossary,
                                                                                         tmp_path):
    w = glossary_drafted(db_session, tmp_path)
    s, proposals = db_session, ProposalRepository(db_session)
    draft_mappings(w)

    draft = draft_config(s, w.feed_id)
    agent_input = transform_input(s, feed_id=w.feed_id, settings=w.settings, draft=draft)
    s.commit()
    propose_transformations(s, agent=w.ctx.agents.transformer, agent_input=agent_input, draft=draft)
    s.commit()
    cleaned = proposals.list_for_feed(w.feed_id, "transformation", "pending")
    assert cleaned
    mapping_of = {m.payload["source_field"]: m.id for m in proposals.list_for_feed(w.feed_id, "mapping", "pending")}
    for t in cleaned:
        assert proposals.bases(t.id) == [mapping_of[t.payload["source_field"]]]

    draft = draft_config(s, w.feed_id)
    agent_input = rules_input(s, feed_id=w.feed_id, catalogue=_catalogue(s, w.settings), draft=draft)
    s.commit()
    backtest = backtest_for(agent_input, connector_factory=w.ctx.connector, blob_store=w.ctx.blob_store,
                            rule_timeout_seconds=w.settings.rule_timeout_seconds)
    record_rules(s, agent=w.ctx.agents.rule_recommender, agent_input=agent_input, backtest=backtest, draft=draft)
    s.commit()
    rules = proposals.list_for_feed(w.feed_id, "rule", "pending")
    assert rules
    assert all(r.payload["backtest"]["status"] == "ran" for r in rules), "back-tested on the draft mappings"
    assert all(proposals.bases(r.id) for r in rules), "every rule names the draft mappings it was built on"
    assert configuration_rows(db_session, w.feed_id) == {"mappings": 0, "transformations": 0, "rules": 0}


def test_a_draft_that_changes_during_the_call_is_discarded(db_session, glossary, tmp_path):
    w = glossary_drafted(db_session, tmp_path)
    s = db_session
    draft = draft_config(s, w.feed_id)
    used = next(iter(draft.element_bases.values()))
    check_unchanged(s, draft)  # nothing changed yet
    original = ProposalRepository(s).get(used)
    edit_item(s, session_id=w.session_id, proposal_id=used, payload=dict(original.payload, reason="Edited."),
              user=OPERATOR)
    s.flush()
    with pytest.raises(ConfigurationChangedMeanwhile):
        check_unchanged(s, draft)
