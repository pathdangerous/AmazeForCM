"""Task 5.3 (phase 7.8, assumptions 31 to 39): the orchestration of one onboarding session, on the mock provider,
through the real step functions, with the security master from the intake (decision D8).

One operator (dev-operator, every backend role), four-eyes OFF unless a test says otherwise. The worker body is
run_job(): the tests call it the way the worker will (task 5.4), with the db_session as the one session.
"""

from __future__ import annotations

import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from agents.glossary_builder import GlossaryBuilderAgent
from agents.mapping import MappingAgent
from agents.profiler import ProfilerAgent
from agents.review_summariser import ReviewSummariserAgent
from agents.rule_recommender import RuleRecommenderAgent
from agents.transformation import TransformationAgent
from apps.api.jobs import DailyFetch
from core.domain.onboarding import ONBOARDING_STEPS, ApproveOptions
from core.domain.proposal import Proposal
from core.proposal_log import ProposalLogEntry
from core.repositories.errors import (
    BackNotAllowed,
    DecisionNeeded,
    StepJobActive,
    StepNotEnterable,
    StepNotReady,
)
from core.repositories.feed_profiles import FeedProfileRepository
from core.repositories.feed_registrations import FeedRegistrationRepository
from core.repositories.feeds import FeedRepository
from core.repositories.go_lives import GoLiveRepository
from core.repositories.onboarding import OnboardingJobRepository, OnboardingRepository
from core.repositories.products import ProductRepository
from core.repositories.proposals import ProposalRepository
from core.repositories.settings import FourEyesRepository
from core.repositories.transformations import TransformationRepository
from core.steps.onboarding import (
    OnboardingContext,
    StepAgents,
    approve_and_next,
    back,
    confidence_of,
    edit_item,
    enter_step,
    run_job,
    start_session,
    timeline,
    workspace,
)
from core.steps.s00_intake import confirm_upload, inspect_upload
from core.steps.s05_glossary import decide_element
from core.steps.s06_map import decide_mapping
from core.steps.s09_rules import decide_rule
from core.steps.s11_go_live import decide_go_live
from ports.config import Settings
from ports.connectors.dropzone import DropzoneConnector
from ports.identity.dev import DevIdentity
from ports.llm.base import LLMUnavailable
from ports.llm.mock import MockProvider
from ports.notify.inapp import InAppNotifier
from ports.storage.local import LocalBlobStore

DEMO = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic" / "Synthetic_Security_Master.xlsx"
IDENTITY = DevIdentity()
OPERATOR = IDENTITY.get_user("dev-operator")
CHECKER = IDENTITY.get_user("dev-checker")


# ---------------------------------------------------------------- providers that make the cases the mock does not


class Doubting:
    """The MockProvider, except that the transformation agent doubts the first column it cleans (confidence 0.5) and
    plain mock for every other agent, rules included (task 5.3b)."""

    name = "doubting"

    def __init__(self) -> None:
        self.inner = MockProvider()

    def complete(self, **kw):
        result = self.inner.complete(**kw)
        if kw["prompt_id"] != "transformation":
            return result
        data, doubted = result.output.model_dump(), False
        for answer in data["columns"]:
            if answer["steps"] and not doubted:
                answer["confidence"], doubted = 0.5, True
        return result.model_copy(update={"output": kw["response_schema"].model_validate(data)})


class Flaky:
    """Fails its first `times` calls with `error`, then answers as `inner`."""

    name = "flaky"

    def __init__(self, inner, *, times: int, error: Exception) -> None:
        self.inner, self.left, self.error, self.calls = inner, times, error, 0

    def complete(self, **kw):
        self.calls += 1
        if self.left:
            self.left -= 1
            raise self.error
        return self.inner.complete(**kw)


class During:
    """Runs `hook` where the model would be thinking, then answers as the mock does."""

    name = "during"

    def __init__(self, hook) -> None:
        self.inner, self.hook = MockProvider(), hook

    def complete(self, **kw):
        self.hook()
        return self.inner.complete(**kw)


# ---------------------------------------------------------------- the world: an uploaded security master


@dataclass
class World:
    session: object
    sf: object
    ctx: OnboardingContext
    feed_id: UUID
    session_id: UUID
    settings: Settings


def make_world(db_session, tmp_path, *, provider=None, four_eyes: bool = False, **settings_over) -> World:
    FourEyesRepository(db_session).set(four_eyes, set_by="test", reason="Synthetic.")
    db_session.commit()
    blobs, dropzone = LocalBlobStore(tmp_path / "blob"), tmp_path / "dropzone"
    upload = inspect_upload(DEMO.read_bytes(), filename=DEMO.name, blob_store=blobs, max_bytes=10**8)
    db_session.commit()
    uploaded = confirm_upload(
        db_session, upload_id=upload.upload_id, filename=DEMO.name, feed_code="syn_security_master",
        feed_name="Security master", sheet=None, header_row=None, blob_store=blobs, user=OPERATOR,
        notifier=InAppNotifier(db_session), backend_team=["dev-operator"], dropzone_root=dropzone)
    db_session.commit()
    settings = Settings(_env_file=None, dropzone_root=dropzone, **settings_over)

    def connectors(name: str):
        assert name == "dropzone", name
        return DropzoneConnector(dropzone)

    sf = lambda: nullcontext(db_session)  # noqa: E731 (the one session, as test_jobs.py does)
    daily = DailyFetch(
        session_factory=sf, blob_store=blobs, connector_factory=connectors, settings=settings,
        backend_team=lambda: ["dev-operator"], operations=lambda: ["dev-operator"],
        roles_of=lambda user_id: IDENTITY.get_user(user_id).roles,
        display_name=lambda user_id: IDENTITY.get_user(user_id).display_name,
        trust_release_threshold=settings.trust_release_threshold)
    brain, notes = provider or MockProvider(), LocalBlobStore(tmp_path / "agent-blob")
    agents = StepAgents(
        profiler=ProfilerAgent(provider=brain, blob_store=notes),
        glossary_builder=GlossaryBuilderAgent(provider=brain, blob_store=notes),
        mapper=MappingAgent(provider=brain, blob_store=notes),
        transformer=TransformationAgent(provider=brain, blob_store=notes),
        rule_recommender=RuleRecommenderAgent(provider=brain, blob_store=notes),
        summariser=ReviewSummariserAgent(provider=brain, blob_store=notes))
    ctx = OnboardingContext(agents=agents, blob_store=blobs, settings=settings, identity=IDENTITY,
                            load_now=daily.fetch_and_load, connector_factory=connectors)
    started = start_session(db_session, feed_id=uploaded.feed_id, user=OPERATOR)
    db_session.commit()
    return World(session=db_session, sf=sf, ctx=ctx, feed_id=uploaded.feed_id, session_id=started.id,
                 settings=settings)


def work(w: World, **kw):
    """The worker: every queued job, one after the other."""
    ran = []
    while (result := run_job(w.sf, ctx=w.ctx, **kw)) is not None:
        ran.append(result)
    return ran


def current(w: World):
    return OnboardingRepository(w.session).get(w.session_id)


def view(w: World):
    ws = workspace(w.session, session_id=w.session_id, settings=w.settings)
    for item in ws.items:  # phase 7.8, task 7.3a: every item says why, whatever its kind
        assert item.reason and item.reason.strip(), (item.kind, item.column, item.payload)
    return ws


def approve(w: World, user=OPERATOR, **kw):
    advance = approve_and_next(w.session, session_id=w.session_id, user=user, ctx=w.ctx, **kw)
    w.session.commit()
    return advance


def events(w: World, event: str | None = None, step: str | None = None):
    found = OnboardingRepository(w.session).events(w.session_id)
    return [e for e in found if (event is None or e.event == event) and (step is None or e.step == step)]


def settle(w: World, flagged_rules: str = "rejected") -> int:
    """Decide, as the operator would with the existing decision calls, every mapping, glossary or rule item of the step
    that only a person can (a tie, a conflict, a rule that flags rows of this file: task 5.3b). flagged_rules is what
    the operator does with such a rule: "rejected" (it is wrong for this data) or "approved" (kept knowingly). Returns
    how many were decided."""
    decided = 0
    for item in view(w).items:
        if not item.needs_decision:
            continue
        notifier = InAppNotifier(w.session)
        if item.kind == "rule":
            decide_rule(w.session, proposal_id=item.proposal_id, user=OPERATOR, decision=flagged_rules,
                        note="flags rows that are valid in this file" if flagged_rules == "rejected" else None)
        elif item.kind == "mapping":
            decide_mapping(w.session, proposal_id=item.proposal_id, user=OPERATOR, decision="approved",
                           choice=item.payload["candidates"][0], note=None)
        elif item.kind == "glossary_element":
            decide_element(w.session, proposal_id=item.proposal_id, user=OPERATOR, decision="approved", choice=0,
                           note=None, notifier=notifier, stewards=[])
        else:
            continue  # an item the test edits itself
        decided += 1
    w.session.commit()
    return decided


def run_to(w: World, step: str, flagged_rules: str = "rejected") -> None:
    """Approve & next, with the worker between, until `step` is current and its job has answered."""
    work(w)
    while current(w).current_step != step:
        settle(w, flagged_rules)
        approve(w)
        work(w)
    settle(w, flagged_rules)


# ---------------------------------------------------------------- the confidence of a proposal (assumption 35)


def proposal(kind: str, payload: dict, **more) -> Proposal:
    return Proposal(id=uuid4(), created_at=datetime.now(UTC), proposal_log_id=uuid4(), kind=kind, payload=payload,
                    autonomy="A2", required_approver_roles=["data_steward"], status="pending", decided_at=None,
                    requirement_id=None, feed_id=uuid4(), **more)


def test_auto_accept_confidence_defaults_to_0_85_and_is_read_from_the_environment(monkeypatch):
    assert Settings(_env_file=None).auto_accept_confidence == 0.85
    monkeypatch.setenv("AUTO_ACCEPT_CONFIDENCE", "0.9")
    assert Settings(_env_file=None).auto_accept_confidence == 0.9


def test_confidence_is_the_payloads_own_when_it_has_one():
    assert confidence_of(proposal("transformation", {"confidence": 0.6})) == 0.6
    assert confidence_of(proposal("profile_summary", {"confidence": 0.9, "recommended_outcome": "meets"})) == 0.9


def test_a_summary_without_a_confidence_is_certain_and_a_recommended_rejection_is_for_a_person():
    assert confidence_of(proposal("profile_summary", {"recommended_outcome": "meets"})) == 1.0
    assert confidence_of(proposal("review_summary", {})) == 1.0
    assert confidence_of(proposal("profile_summary", {"recommended_outcome": "rejected", "confidence": 0.99})) is None


def test_a_glossary_conflict_is_half_sure_and_any_other_glossary_proposal_is_certain():
    assert confidence_of(proposal("glossary_element", {"outcome": "conflict_to_owner"})) == 0.5
    assert confidence_of(proposal("glossary_element", {"outcome": "proposed"})) == 1.0


def test_a_rule_is_as_sure_as_its_back_test_on_the_current_file_says():
    """Task 5.3b: 1.0 when it flags nothing in the current file, else 1 - flagged/rows to two decimals; no usable
    current-file delivery leaves today's value (certain)."""
    def rule(*deliveries):
        return proposal("rule", {"rule_key": "r", "backtest": {"status": "ran", "deliveries": list(deliveries)}})

    own = {"label": "current file", "rows": 70, "flagged": 0, "excluded": None}
    assert confidence_of(rule(own)) == 1.0
    assert confidence_of(rule({**own, "flagged": 34})) == 0.51
    assert confidence_of(rule({"label": "load 2026-11-01", "rows": 10, "flagged": 9, "excluded": None})) == 1.0
    assert confidence_of(rule({**own, "rows": 0, "flagged": 0, "excluded": "file_missing"})) == 1.0
    assert confidence_of(proposal("rule", {"rule_key": "r"})) == 1.0


def test_a_mapping_is_as_sure_as_its_candidates_score_and_a_tie_is_for_a_person():
    one = proposal("mapping", {"source_field": "A", "candidates": ["x"], "recommended": "x"})
    assert confidence_of(one, {"A": {"x": 0.93, "y": 0.4}}) == 0.93
    tie = proposal("mapping", {"source_field": "A", "candidates": ["x", "y"], "recommended": None})
    assert confidence_of(tie, {"A": {"x": 0.8, "y": 0.8}}) is None
    level = proposal("mapping", {"source_field": "A", "candidates": ["x"], "recommended": "x"})
    assert confidence_of(level, {"A": {"x": 0.8, "y": 0.8}}) is None  # the top two scores are equal: a tie
    assert confidence_of(one) is None  # no score known: a person looks


# ---------------------------------------------------------------- the whole path, with an edit and a Back


def test_the_security_master_goes_from_upload_to_product_ready_with_one_edit_and_one_back(db_session, glossary,
                                                                                     tmp_path):
    # The plain mock's rules at the default release threshold (task 5.3b): step 09's back-test runs on the master's own
    # file, so the presence rules on its empty or placeholder columns are returned; the "not negative" rules flag rows
    # where net debt and the percent changes are legitimately negative, so they need a decision and the operator
    # rejects them. The held case (the operator keeps them) has its own test.
    w = make_world(db_session, tmp_path, provider=Doubting())

    # start_session: upload done, profile queued
    first = current(w)
    assert (first.current_step, first.status, first.created_by) == ("profile", "active", "dev-operator")
    assert first.step_states["upload"] == "done" and first.step_states["profile"] == "agent_working"
    assert [(e.event, e.step) for e in events(w)] == [("entered", "profile"), ("agent_queued", "profile")]
    with pytest.raises(StepNotReady):  # nothing to approve while the agent works
        approve(w)
    db_session.rollback()

    # profile: the agent summarises; its key is named like an identifier (0.9), so it is pre-accepted
    ran = work(w)
    assert [(r.step, r.status) for r in ran] == [("profile", "done")]
    ws = view(w)
    assert ws.current_step == "profile" and ws.steps[1].state == "ready"
    [item] = ws.items
    assert item.kind == "profile_summary" and item.pre_accepted and not item.needs_decision
    assert item.confidence == pytest.approx(0.9)
    assert [e.actor for e in events(w, "pre_accepted")] == ["system"]
    assert ws.model_calls == 1 and ws.model_call_cap == 60
    [note] = ws.agent_notes
    assert (note.agent, note.provider, note.prompt_id) == ("ProfilerAgent", "mock", "profiler")
    assert note.input_ref.startswith("agent-inputs/ProfilerAgent/") and note.reasoning

    advance = approve(w, options=ApproveOptions(visibility="restricted"))
    assert advance.moved and current(w).current_step == "glossary"
    feed = FeedRepository(db_session).get(w.feed_id)
    assert feed.stage == "registered" and feed.owner_id == "dev-operator"
    registration = FeedRegistrationRepository(db_session).current(w.feed_id)
    assert (registration.owner_id, registration.visibility, registration.registered_by) == (
        "dev-operator", "restricted", "dev-operator")
    assert current(w).step_states["profile"] == "done"

    # glossary, mapping: the existing glossary run and mapping step, decided in one click each
    run_to(w, "transformations")
    assert current(w).step_states["glossary"] == "done" and current(w).step_states["mapping"] == "done"

    # transformations: one item the agent doubts needs a person; Approve & next says which
    ws = view(w)
    doubted = [i for i in ws.items if i.needs_decision]
    assert len(doubted) == 1 and doubted[0].kind == "transformation" and doubted[0].confidence == 0.5
    assert ws.steps[4].state == "needs_decision" and not ws.can_approve and "decision" in ws.blocked_by
    with pytest.raises(DecisionNeeded) as refused:
        approve(w)
    db_session.rollback()
    assert [i.proposal_id for i in refused.value.items] == [doubted[0].proposal_id]
    assert ws.key_policy["policy"] == "flag"

    # Back to mapping: the later steps are stale, mapping runs again, then forward again
    back(db_session, session_id=w.session_id, to_step="mapping", user=OPERATOR)
    db_session.commit()
    after_back = current(w)
    assert after_back.current_step == "mapping" and after_back.step_states["mapping"] == "agent_working"
    assert after_back.step_states["transformations"] == "stale"
    assert after_back.step_states["rules"] == "waiting" and after_back.step_states["go_live"] == "waiting"
    assert [e.step for e in events(w, "stale")] == ["transformations"]
    assert [e.event for e in events(w, step="mapping")].count("agent_queued") == 2
    rejected = ProposalRepository(db_session).list_for_feed(w.feed_id, "transformation", "rejected")
    assert len(rejected) == 0  # the pending ones of the stale step are rejected when it is entered again, not now
    work(w)
    assert current(w).step_states["mapping"] == "ready"
    approve(w)
    assert current(w).current_step == "transformations"
    pending = ProposalRepository(db_session).list_for_feed(w.feed_id, "transformation", "pending")
    rejected = ProposalRepository(db_session).list_for_feed(w.feed_id, "transformation", "rejected")
    assert rejected and all(a.decided_by == "system" and a.note == "rerun after Back"
                            for r in rejected for a in ProposalRepository(db_session).approvals(r.id))
    work(w)
    ws = view(w)
    [doubted] = [i for i in ws.items if i.needs_decision]
    assert doubted.proposal_id not in {r.id for r in rejected}

    # the edit: the operator changes the steps; the edited proposal is accepted by the person who edited it
    edited_payload = {**doubted.payload, "steps": [{"op": "trim"}, *doubted.payload["steps"]]}
    edited = edit_item(db_session, session_id=w.session_id, proposal_id=doubted.proposal_id, payload=edited_payload,
                       user=OPERATOR)
    db_session.commit()
    assert edited.edited_from == doubted.proposal_id and edited.edited_by == "dev-operator"
    assert ProposalRepository(db_session).get(doubted.proposal_id).status == "superseded"
    [edit_event] = events(w, "edited")
    assert (edit_event.actor, edit_event.proposal_id) == ("dev-operator", edited.id)
    assert edit_event.detail["edited_by"] == "dev-operator"
    assert [e.actor for e in events(w, "pre_accepted") if e.proposal_id == edited.id] == ["dev-operator"]
    ws = view(w)
    mine = next(i for i in ws.items if i.proposal_id == edited.id)
    assert mine.edited and mine.edited_by == "dev-operator" and mine.pre_accepted and not mine.needs_decision
    assert ws.steps[4].state == "ready" and ws.can_approve

    approve(w)
    assert current(w).current_step == "rules"
    stored = {t.source_field: t for t in TransformationRepository(db_session).current_for_feed(w.feed_id)}
    assert any(t.steps[0].op == "trim" for t in stored.values())

    # two side paths from the rules step, each undone before the walk goes on: four-eyes on, and a held first load
    work(w)  # the rules job answers
    with side_path(db_session):
        four_eyes_on_the_rules_wait_for_a_second_person_who_then_moves_the_step_on(w)
    with side_path(db_session):
        a_first_load_that_is_held_fails_the_step_and_shows_the_outcome(w)
    with side_path(db_session):
        a_go_live_pins_the_configuration_until_the_first_load_has_failed(w)
    assert current(w).current_step == "rules" and not events(w, "agent_failed")  # all undone
    assert FourEyesRepository(db_session).current() is False

    # rules, go-live: the rules need the steward then the checker (one person, four-eyes off); the owner decides
    run_to(w, "go_live")
    assert current(w).step_states["rules"] == "done"
    ws = view(w)
    [summary] = ws.items
    assert summary.kind == "review_summary" and summary.status == "pending"
    # side path: the go-live item is rejected, then a visibility is sent with nothing pending: StepNotReady, no write
    with side_path(db_session):
        decide_go_live(db_session, proposal_id=summary.proposal_id, user=OPERATOR, decision="rejected", note="No.",
                       back_to_step="06", today=date.today(), hypercare_days=w.ctx.settings.hypercare_days,
                       daily_run_utc=w.ctx.daily_run_utc, notifier=InAppNotifier(db_session), backend_team=[],
                       operations=[], role_holders=lambda role: [])
        db_session.commit()
        versions = FeedRegistrationRepository(db_session).current(w.feed_id).version
        with pytest.raises(StepNotReady, match="no go-live item is pending"):
            approve(w, options=ApproveOptions(visibility="restricted"))
        db_session.rollback()
        assert FeedRegistrationRepository(db_session).current(w.feed_id).version == versions
    assert view(w).items[0].status == "pending"  # undone
    approve(w)
    assert current(w).current_step == "first_load" and current(w).step_states["go_live"] == "done"
    for to_step in ("profile", "upload", "mapping", "go_live"):  # nothing goes back once the owner approved go-live
        with pytest.raises(BackNotAllowed):
            back(db_session, session_id=w.session_id, to_step=to_step, user=OPERATOR)
        db_session.rollback()

    # first load: the newest file of the drop folder, steps 12, 13, 15 and 20; it moves on by itself
    calls_before = current(w).model_calls
    ran = work(w)
    assert [(r.step, r.status) for r in ran] == [("first_load", "done")], [r.detail for r in ran]
    done = current(w)
    assert done.status == "done" and done.current_step == "product_ready"
    assert done.step_states["first_load"] == "done" and done.step_states["product_ready"] == "done"
    assert done.model_calls == calls_before  # no model call loads a file (rule 2)
    assert ProductRepository(db_session).for_feed(w.feed_id) is not None
    from core.repositories.loads import LoadRepository
    released = [x for x in LoadRepository(db_session).list_for_feed(w.feed_id) if x.status == "released"]
    assert len(released) == 1
    from core.repositories.certifications import LoadCertificationRepository
    certification = LoadCertificationRepository(db_session).for_load(released[0].id)
    # only the duplicated ticker's two rows are flagged (duplicate_key and one_row_per_ticker on the same rows), none by
    # a presence rule (returned by the back-test) or a "not negative" rule (rejected by the operator)
    assert certification.outcome == "released" and (certification.flagged_rows, certification.rows) == (2, 70)
    assert not any(k.endswith("_present") and n for k, n in certification.rule_counts.items())
    with pytest.raises(StepNotEnterable):
        enter_step(db_session, session_id=w.session_id, step="product_ready", user=OPERATOR)
    db_session.rollback()

    # the audit timeline: every proposal, edit and approval, with who and when
    trail = timeline(db_session, session_id=w.session_id)
    kinds = {t.kind for t in trail}
    assert kinds == {"event", "proposal", "edit", "approval"}
    assert all(t.at is not None for t in trail)
    proposals_seen = {t.proposal_id for t in trail if t.kind == "proposal"}
    every = {p.id for kind in ("profile_summary", "glossary_element", "mapping", "transformation", "rule",
                               "review_summary")
             for p in ProposalRepository(db_session).list_for_feed(w.feed_id, kind) if p.edited_from is None}
    assert every <= proposals_seen
    made = next(t for t in trail if t.kind == "proposal" and t.detail["agent"] == "ProfilerAgent")
    assert made.detail["prompt_version"] and made.detail["model"] and made.detail["latency_ms"] is not None
    [edit] = [t for t in trail if t.kind == "edit"]
    assert edit.actor == "dev-operator" and edit.proposal_id == edited.id and edit.detail["edited_from"] == str(
        doubted.proposal_id)
    approvers = {(t.actor, t.detail["decision"]) for t in trail if t.kind == "approval"}
    assert ("dev-operator", "approved") in approvers and ("system", "rejected") in approvers
    assert [t.at for t in trail if t.kind == "event"] == sorted(t.at for t in trail if t.kind == "event")
    names = [t.title for t in trail if t.kind == "event"]
    assert {"entered", "agent_queued", "agent_started", "agent_done", "pre_accepted", "edited", "approved", "back",
            "stale", "done"} <= set(names)


# ---------------------------------------------------------------- the call cap, a failed call, a late answer


def test_a_job_that_would_pass_the_call_cap_is_not_run(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path, max_model_calls_per_onboarding=1)
    work(w)  # profile: one call, the cap is reached
    assert current(w).model_calls == 1
    approve(w)
    ran = work(w)
    assert [(r.step, r.status) for r in ran] == [("glossary", "discarded")]
    capped = events(w, "call_cap_reached")
    assert len(capped) == 1 and capped[0].step == "glossary" and capped[0].detail["cap"] == 1
    after = current(w)
    assert after.step_states["glossary"] == "failed" and after.model_calls == 1
    assert OnboardingJobRepository(db_session).list_for_session(w.session_id)[-1].status == "discarded"
    assert "call cap" in view(w).progress
    assert not events(w, "agent_started", "glossary")  # no model was asked


def test_a_model_that_is_down_fails_the_step_and_entering_it_again_works(db_session, glossary, tmp_path):
    flaky = Flaky(MockProvider(), times=1, error=LLMUnavailable("timeout"))
    w = make_world(db_session, tmp_path, provider=flaky)
    ran = work(w)
    assert [(r.step, r.status) for r in ran] == [("profile", "failed")]
    assert current(w).step_states["profile"] == "failed"
    [failed] = events(w, "agent_failed")
    assert failed.detail["reason"] == "timeout" and failed.detail["error"] == "LLMUnavailable"
    assert current(w).model_calls == 1  # the call that failed was still a call
    assert "timeout" in view(w).progress
    with pytest.raises(StepNotReady):
        approve(w)
    db_session.rollback()

    enter_step(db_session, session_id=w.session_id, step="profile", user=OPERATOR)
    db_session.commit()
    assert current(w).step_states["profile"] == "agent_working"
    ran = work(w)
    assert [(r.step, r.status) for r in ran] == [("profile", "done")]
    assert current(w).step_states["profile"] == "ready" and current(w).model_calls == 2
    assert FeedProfileRepository(db_session).latest(w.feed_id).version == 1  # the file was not profiled twice
    assert flaky.calls == 2


def test_an_answer_that_arrives_after_its_lease_was_taken_again_is_discarded(db_session, glossary, tmp_path):
    def lease_passes_and_another_worker_takes_the_job():
        time.sleep(1.2)
        taken = OnboardingJobRepository(db_session).claim(lease_seconds=300)
        db_session.commit()
        assert taken is not None and taken.attempt == 2

    w = make_world(db_session, tmp_path, provider=During(lease_passes_and_another_worker_takes_the_job))
    # the claim in run_job() has a one second lease
    [late] = work(w, lease_seconds=1)[:1]
    assert (late.step, late.status) == ("profile", "superseded")
    assert not ProposalRepository(db_session).list_for_feed(w.feed_id, "profile_summary")
    # The call is logged, not used. (Its discard row is committed on Agent.run()'s own connection; here every session
    # shares one connection, so the worker's rollback takes it back too: tests/test_concurrency.py proves the row.)
    assert db_session.scalar(select(func.count()).select_from(ProposalLogEntry)) == 1
    active = OnboardingJobRepository(db_session).active_for_session(w.session_id)
    assert active is not None and active.status == "running" and active.attempt == 2
    assert current(w).step_states["profile"] == "agent_working"


# ---------------------------------------------------------------- enter, refused


def test_only_the_current_step_is_entered_and_never_while_a_job_is_active(db_session, glossary, tmp_path):
    w = make_world(db_session, tmp_path)
    with pytest.raises(StepNotEnterable):
        enter_step(db_session, session_id=w.session_id, step="mapping", user=OPERATOR)
    db_session.rollback()
    with pytest.raises(StepJobActive):
        enter_step(db_session, session_id=w.session_id, step="profile", user=OPERATOR)
    db_session.rollback()
    with pytest.raises(BackNotAllowed):  # nothing before the glossary goes back: the feed is not registered yet
        back(db_session, session_id=w.session_id, to_step="profile", user=OPERATOR)
    db_session.rollback()
    assert [s.name for s in view(w).steps] == list(ONBOARDING_STEPS)


# ---------------------------------------------------------------- side paths of the one walk (the test-time ceiling)
# Until phase 7.8's cost cut these were two tests of their own, each walking the master from upload to the rules step
# again; they are now taken from the full walk's rules step and undone before it goes on (side_path).


@contextmanager
def side_path(session):
    """A path tried from where the walk is, then undone: a savepoint of the test's connection, outside the session's
    own savepoints (join_transaction_mode create_savepoint), rolled back at the end."""
    session.commit()
    nested = session.get_bind().begin_nested()
    try:
        yield
    finally:
        if session.in_transaction():
            session.rollback()
        nested.rollback()
        session.expire_all()


def four_eyes_on_the_rules_wait_for_a_second_person_who_then_moves_the_step_on(w: World) -> None:
    """At the rules step (its job has answered), with four-eyes switched on. The steps before the rules have one
    role each, so turning it on here tests what the earlier separate test did with it on from the start."""
    db_session = w.session
    FourEyesRepository(db_session).set(True, set_by="test", reason="Synthetic: the four-eyes side path.")
    db_session.commit()
    settle(w)  # the rules that flag rows of this file are rejected by the operator (task 5.3b)
    ws = view(w)
    kept = [i for i in ws.items if i.status != "rejected"]
    assert kept and all(i.kind == "rule" and i.status == "pending" for i in kept)

    advance = approve(w)  # the operator is the steward; a checker is another person
    assert not advance.moved and advance.waiting_for == "a second person"
    assert current(w).current_step == "rules" and current(w).step_states["rules"] == "needs_decision"
    ws = view(w)
    assert all(i.waiting_for == "a second person" and not i.needs_decision for i in ws.items if i.status != "rejected")
    assert "second person" in ws.steps[5].detail
    again = approve(w)  # the same person again changes nothing
    assert not again.moved and again.waiting_for == "a second person"

    done = approve(w, user=CHECKER)
    assert done.moved and current(w).current_step == "go_live"
    approvals = [(a.role, a.decided_by) for i in ws.items for a in ProposalRepository(db_session).approvals(i.proposal_id)]
    assert ("data_steward", "dev-operator") in approvals and ("checker", "dev-checker") in approvals


def a_first_load_that_is_held_fails_the_step_and_shows_the_outcome(w: World) -> None:
    """At the rules step: the default release threshold; the operator keeps the rules that flag rows of this file
    (task 5.3b), so they hold the first load."""
    db_session = w.session
    run_to(w, "go_live", flagged_rules="approved")
    approve(w)
    [ran] = work(w)
    assert (ran.step, ran.status) == ("first_load", "failed")
    after = current(w)
    assert after.step_states["first_load"] == "failed" and after.status == "active"
    assert after.current_step == "first_load" and after.step_states["product_ready"] == "waiting"
    ws = view(w)
    assert "held" in ws.progress and "trust score" in ws.progress and ws.steps[7].detail == ws.progress
    assert not ws.can_approve
    # follow-up item 2: the panel's facts and message are the latest load's; earlier loads are timeline lines
    assert ws.inputs.load_summary and "held" in ws.inputs.load_summary
    assert {f.label: f.value for f in ws.inputs.facts}["Load"] == "held"
    assert [t.title for t in timeline(db_session, session_id=w.session_id) if t.step == "first_load" and t.title.startswith("load ")] == ["load held"]
    assert ProductRepository(db_session).for_feed(w.feed_id) is None
    [failed] = events(w, "agent_failed")
    assert failed.step == "first_load" and failed.detail["reason"] == "step_failed"
    # follow-up item 1: go-live pins the configuration, but a first load that did not release may go back to the
    # rules, and to nothing else (the owner then approves a next go-live version)
    for other in ("glossary", "mapping", "transformations", "go_live"):
        with pytest.raises(BackNotAllowed):
            back(db_session, session_id=w.session_id, to_step=other, user=OPERATOR)
        db_session.rollback()
    went = back(db_session, session_id=w.session_id, to_step="rules", user=OPERATOR)
    db_session.commit()
    assert went.current_step == "rules" and went.step_states["go_live"] == "stale"
    assert went.step_states["first_load"] == "stale" and went.step_states["product_ready"] == "waiting"
    last = events(w, "back")[-1]  # the walk went back once before (to the mapping)
    assert (last.step, last.detail.get("from")) == ("rules", "first_load")
    assert ProductRepository(db_session).for_feed(w.feed_id) is None

    # and the way on works against the guards (versions, not edits): the rules again, a next go-live version, the load
    work(w)  # the rules job answers again
    settle(w, "rejected")  # this time the operator rejects the rules that flag rows
    approve(w)
    work(w)  # the go-live step: a test load and the summary
    assert current(w).current_step == "go_live"
    approve(w)
    assert GoLiveRepository(db_session).latest(w.feed_id).version == 2  # a new version; version 1 is untouched
    [again] = work(w)
    assert (again.step, again.status) == ("first_load", "failed")  # the same file: step 12 quarantines it
    assert "quarantined" in view(w).progress
    assert ProductRepository(db_session).for_feed(w.feed_id) is None


def a_go_live_pins_the_configuration_until_the_first_load_has_failed(w: World) -> None:
    """Before the first load has run (or while it is ready to run) nothing goes back, as before."""
    run_to(w, "go_live", flagged_rules="approved")
    approve(w)  # the go-live is approved; the first load is now the current step and has not run
    assert current(w).current_step == "first_load" and current(w).step_states["first_load"] != "failed"
    with pytest.raises(BackNotAllowed):
        back(w.session, session_id=w.session_id, to_step="rules", user=OPERATOR)
    w.session.rollback()
