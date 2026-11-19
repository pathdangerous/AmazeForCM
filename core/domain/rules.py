"""Step 09, validation rules (phase 6 plan, assumptions 5, 7, 8, 10 and 11; section 7).

A rule is one SQL SELECT over the table `curated` (one column per mapped element key, plus `_row`) that returns the
`_row` of every row that breaks it. The agent sees a small input: per mapped element its type, null rate, distinct
count and at most 5 rule templates that fit it (core/steps/rule_candidates.py), never the whole glossary and never
the SQL of the rules the feed has. Code, not the agent, fills the back-test and the verdict of each rule it
answers with (Agent.run(after_answer=...)).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from core.domain.common import NonBlank, SnakeName
from core.domain.glossary import DataType
from core.domain.transformation import Transformation

Verdict = Literal["kept", "returned", "refused"]
MAX_TEMPLATES = 5  # per element
MAX_KNOWN_KEYS = 20  # the keys of the feed's current and pending rules sent to the agent
MAX_SHARE = 0.5  # a rule that flags more than this share of the history is returned (assumption 10)


class RuleTemplateOffer(BaseModel):
    """One rule a template makes for these elements: the agent may use it as it is or write its own."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template: str  # present, not_negative, in_whole_lots, not_in_future, one_row_per_key
    rule_key: SnakeName
    description: NonBlank
    sql: NonBlank
    element_keys: list[str]


class RuleCandidateElement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_field: str  # the file column the element is mapped from (the back-test needs it)
    key: str
    data_type: DataType
    null_rate: float | None
    distinct_count: int | None
    templates: list[RuleTemplateOffer] = Field(max_length=MAX_TEMPLATES)


class LoadHistoryItem(BaseModel):
    """One of the feed's own past scheduled loads: where its file is in the store, never its content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str  # "load <received date>"
    file_ref: str


class RuleRecommenderInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    feed_id: UUID
    feed_code: str
    source_name: str
    elements: list[RuleCandidateElement]
    feed_templates: list[RuleTemplateOffer] = Field(max_length=MAX_TEMPLATES)  # rules over several elements
    existing_keys: list[str] = Field(max_length=MAX_KNOWN_KEYS)  # current and pending rules: keys only
    rejected_keys: list[str]  # rules a steward or checker rejected: not proposed again
    mapping_ids: list[UUID]  # the mappings the input was built from
    connector: str | None  # how the feed's source and its history are read
    history: list[str]  # source refs of the synthetic past deliveries (empty: not back-tested)
    # Phase 7: the feed's newest scheduled loads that completed all four stages, read from the blob store.
    load_history: list[LoadHistoryItem] = Field(default_factory=list)
    # Phase 7.8, task 5.3b: the feed's latest fetched file (the one the profile was made from), so that a first
    # onboarding, with no past load, still has history to back-test on.
    current_file: LoadHistoryItem | None = None
    # Phase 7.8, task 4.3: the feed's approved transformations. Code reads the history through them (as the engine
    # would load it); the model is not told, so the field is left out of what it is sent and what is stored.
    transformations: list[Transformation] = Field(default_factory=list, exclude=True)


class Delivery(BaseModel):
    """What one rule did on one past delivery, or why the delivery was left out."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    rows: int
    flagged: int
    excluded: str | None = None  # the reason, when the delivery could not be curated


class Backtest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ran", "not_back_tested"]
    total_rows: int = 0
    flagged_rows: int = 0
    share: float | None = None
    deliveries: list[Delivery] = Field(default_factory=list)


class ProposedRule(BaseModel):
    """What the agent answers; verdict, reason and backtest are filled by code and left empty by the agent."""

    model_config = ConfigDict(extra="forbid")

    rule_key: SnakeName
    description: NonBlank
    sql: NonBlank
    element_keys: list[str]
    template: str | None = None
    verdict: Verdict | None = None
    reason: str | None = None
    backtest: Backtest | None = None


class RuleRecommenderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[ProposedRule]


class RuleProposalPayload(BaseModel):
    """The payload of a `rule` proposal. rule_key, sql and description are what trg_rules_b_matches_proposal
    compares the rules row with."""

    model_config = ConfigDict(extra="forbid")

    rule_key: SnakeName
    sql: NonBlank
    description: NonBlank
    element_keys: list[str]
    template: str | None
    backtest: Backtest
