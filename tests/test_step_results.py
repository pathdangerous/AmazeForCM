"""Typed step results (rule 3; decision 9): each step returns a Pydantic discriminated union on
`outcome`, and the outcome names are CLAUDE.md's, letter for letter. Tasks 4 to 7 add a union each.
"""

import re
from typing import Literal

import pytest
from pydantic import ValidationError

from core.steps.result import StepResult
from ports.config import REPO_ROOT


def claude_md_outcomes(label: str) -> list[str]:
    """The names in backticks on CLAUDE.md's line "- **<label>**: ..." (steps and negative outcomes)."""
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.startswith(f"- **{label}**:")]
    assert len(lines) == 1, f"CLAUDE.md has {len(lines)} lines for {label!r}"
    return re.findall(r"`([a-z_]+)`", lines[0])


def test_claude_md_lists_the_phase_2_outcomes():
    assert claude_md_outcomes("17 Discover") == ["found", "not_entitled_visible"]
    assert claude_md_outcomes("Routing checks") == ["reuse", "compose", "create_product", "raise_requirement"]
    assert claude_md_outcomes("01 Define requirement") == ["defined", "element_not_in_glossary"]


def union_outcomes(union) -> list[str]:
    """The outcome names of a discriminated union, in declaration order."""
    from typing import get_args

    members = get_args(get_args(union)[0])
    return [m.model_fields["outcome"].default for m in members]


def round_trips(union, value) -> None:
    from pydantic import TypeAdapter

    adapter = TypeAdapter(union)
    assert adapter.validate_json(adapter.dump_json(value)) == value
    with pytest.raises(ValidationError):
        adapter.validate_python({**adapter.dump_python(value), "outcome": "lost"})


# ---- step 17 (task 5)


def test_step_17_outcomes_are_claude_mds():
    from core.steps.s17_discover import Hit

    assert union_outcomes(Hit) == claude_md_outcomes("17 Discover")


def test_step_17_hits_round_trip():
    from core.steps.s17_discover import Found, Hit, NotEntitledVisible, ProductSummary

    product = ProductSummary(code="p", name="P", description="", domain="pricing", element_keys=["close_price"])
    round_trips(Hit, Found(product=product))
    round_trips(Hit, NotEntitledVisible(product=product, route="Ask Devon Owner for access to P"))


# ---- routing checks (task 6)


def test_routing_outcomes_are_claude_mds():
    from core.domain.consumer_request import ROUTING_OUTCOMES
    from core.steps.routing import RoutingResult

    assert union_outcomes(RoutingResult) == claude_md_outcomes("Routing checks") == list(ROUTING_OUTCOMES)


def test_routing_results_round_trip():
    from core.steps.routing import Compose, CreateProduct, ProductAccess, RaiseRequirement, Reuse, RoutingResult

    open_one = ProductAccess(code="p", name="P", covers=["close_price"], entitled=True, route=None)
    closed = ProductAccess(code="q", name="Q", covers=["bid_price"], entitled=False, route="Ask Devon Owner for access to Q")
    for value in (
        Reuse(product=open_one),
        Compose(products=[open_one, closed]),
        CreateProduct(elements=["volume"], covered_by=[open_one]),
        RaiseRequirement(elements=["volume"], terms=["esg score"], in_domains=[], covered_by=[closed]),
    ):
        round_trips(RoutingResult, value)


# ---- step 01 (task 7)


def test_step_01_outcomes_are_claude_mds():
    from core.steps.s01_define_requirement import DefineRequirementResult

    assert union_outcomes(DefineRequirementResult) == claude_md_outcomes("01 Define requirement")


def test_step_01_results_round_trip():
    import uuid
    from datetime import UTC, datetime

    from core.domain.requirement import Requirement
    from core.steps.s01_define_requirement import DefineRequirementResult, Defined, ElementNotInGlossary

    requirement = Requirement(id=uuid.uuid4(), created_at=datetime.now(UTC), requester_id="dev-requester",
                              title="T", description="", status="defined", elements=["volume"], terms=["esg score"])
    round_trips(DefineRequirementResult, Defined(requirement=requirement, notified=["dev-backend-team"]))
    round_trips(DefineRequirementResult,
                ElementNotInGlossary(requirement=requirement, terms=["esg score"], notified=[]))


# ---- phase 3a: Known source? (task 3)


def test_known_source_outcomes_are_claude_mds():
    from core.steps.known_source import KnownSourceResult

    assert union_outcomes(KnownSourceResult) == claude_md_outcomes("Known source?")


def test_known_source_results_round_trip():
    from core.domain.sourcing import SourceSummary
    from core.steps.known_source import KnownSource, KnownSourceResult, NoKnownSource
    from ports.connectors.catalogue import SourceConditions

    source = SourceSummary(code="syn_x", name="X", provider="Synthetic", visibility="public",
                           conditions=SourceConditions(licence="L", permitted_use="U", redistribution="R"))
    round_trips(KnownSourceResult, KnownSource(source=source))
    round_trips(KnownSourceResult, NoKnownSource(reason="terms_ticked"))


# ---- phase 3a: S4 fetch (task 5)


def test_s4_outcomes_are_the_last_two_of_claude_mds_s1_to_s4():
    from core.steps.s4_fetch import FetchResult

    assert union_outcomes(FetchResult) == claude_md_outcomes("S1–S4")[-2:] == ["fetched", "source_down"]


def test_s4_results_round_trip():
    import uuid

    from core.steps.s4_fetch import Fetched, FetchResult, SourceDown

    round_trips(FetchResult, Fetched(feed_code="f", fetch_id=uuid.uuid4(), blob_ref="raw/f/x", size_bytes=3,
                                     requirements_onboarding=[uuid.uuid4()]))
    round_trips(FetchResult, SourceDown(feed_code="f", fetch_id=uuid.uuid4(), detail="not answering"))


class Done(StepResult):
    outcome: Literal["done"] = "done"
    detail: str


def test_a_step_result_is_frozen_and_forbids_extra_fields():
    result = Done(detail="x")
    with pytest.raises(ValidationError):
        result.detail = "y"
    with pytest.raises(ValidationError):
        Done(detail="x", surprise=1)
    with pytest.raises(ValidationError):
        Done(outcome="other", detail="x")
