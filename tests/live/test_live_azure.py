"""Live smoke test of the real Azure OpenAI adapter (phase 7.8, task 1.3). Collected only by `pytest -m live`.

One call per agent prompt, with a small synthetic input, straight to AzureOpenAIProvider.complete() (the
no-direct-call check scans core/, apps/ and agents/ only). Assertions only: the model's text is never printed,
and nothing from the environment is read or shown here (the provider reads its own settings). An unconfigured
machine FAILS at the `provider` fixture; there is no skip.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from agents.discovery_assistant import DiscoveryAssistant
from agents.glossary_builder import GlossaryBuilderAgent
from agents.intent import IntentAgent
from agents.mapping import MappingAgent
from agents.profiler import ProfilerAgent
from agents.prompt_loader import PromptLibrary
from agents.registry import PROMPT_VERSIONS
from agents.review_summariser import ReviewSummariserAgent
from agents.rule_recommender import RuleRecommenderAgent
from agents.source_discovery import SourceDiscoveryAgent
from core.domain.glossary import GlossaryElementDraft
from core.domain.go_live import ReviewSummaryInput
from core.domain.mapping import ColumnToMap, GlossaryBuilderInput, MappingInput, NewColumn
from core.domain.profiling import ItemCoverage, ProfilerInput, RequirementCoverage, SourceSpec
from core.domain.rules import RuleRecommenderInput, RuleTemplateOffer
from core.domain.sourcing import (AssistantInput, CandidateRef, ChannelRef, Coverage, ElementRef, GlossaryRef,
                                  IntentInput, SourceDiscoveryInput)
from core.steps.candidates import candidate_list
from ports.llm.azure_openai import AzureOpenAIProvider
from ports.llm.base import LLMResult
from ports.storage.local import LocalBlobStore
from tests.onboarded import COLUMN

pytestmark = pytest.mark.live

MAX_CALLS = 12
SEED = Path(__file__).resolve().parents[2] / "fixtures" / "glossary" / "enterprise_glossary.json"
GLOSSARY = [GlossaryElementDraft(**{k: e[k] for k in ("key", "name", "domain", "data_type", "definition", "aliases")})
            for e in json.loads(SEED.read_text(encoding="utf-8"))["elements"]]
FEED = uuid.uuid4()
calls: list[str] = []


@pytest.fixture(scope="module")
def blobs(tmp_path_factory):
    return LocalBlobStore(tmp_path_factory.mktemp("live-blob"))


@pytest.fixture(scope="module")
def provider(blobs):
    live = AzureOpenAIProvider(blob_store=blobs)
    if not live.available:
        pytest.fail("Azure OpenAI is not configured; missing: " + ", ".join(live.missing()), pytrace=False)
    return live


def ask(provider, blobs, agent_cls, agent_input) -> LLMResult:
    """Render the agent's current prompt with this input and make one real call."""
    assert len(calls) < MAX_CALLS, "the live module may make at most 12 model calls"
    calls.append(agent_cls.prompt_id)
    prompt_id = agent_cls.prompt_id
    prompt = PromptLibrary().get(prompt_id)
    assert isinstance(agent_input, agent_cls.input_schema)
    result = provider.complete(prompt_id=prompt_id, prompt_version=PROMPT_VERSIONS[prompt_id],
                               messages=prompt.render(agent_input), response_schema=agent_cls.output_schema)
    print(f"live {prompt_id}: latency_ms={result.latency_ms} model_version={result.model_version}")
    assert isinstance(result.output, agent_cls.output_schema)
    assert result.latency_ms > 0
    assert result.model_version
    assert result.raw_ref and blobs.exists(result.raw_ref)
    return result


def test_intent(provider, blobs):
    channels = [ChannelRef(channel="exchanges", visibility="public"),
                ChannelRef(channel="market_data_vendors", visibility="public"),
                ChannelRef(channel="internal_systems", visibility="private")]
    ask(provider, blobs, IntentAgent, IntentInput(
        requirement_id=uuid.uuid4(), requirement_text="open price", purpose="Desk analysis", note=None,
        elements=[ElementRef(key="open_price", name="Open price")], terms=[], channels=channels))


def test_source_discovery(provider, blobs):
    candidate = CandidateRef(
        code="syn_vendor_eod_prices", name="Synthetic vendor end-of-day prices", provider="Synthetic Vendor",
        description="Daily open and close prices.", elements=["open_price", "close_price"], other_fields=[],
        conditions={"licence": "Synthetic licence", "permitted_use": "Internal", "redistribution": "No",
                    "external_approvals": []},
        coverage=Coverage(elements=["open_price", "close_price"], terms=[], missing=[]))
    ask(provider, blobs, SourceDiscoveryAgent, SourceDiscoveryInput(
        requirement_id=uuid.uuid4(), requested_elements=["open_price", "close_price"], requested_terms=[],
        scope_visibility="public", where_to_search=["market_data_vendors"], candidates=[candidate]))


def test_discovery_assistant(provider, blobs):
    ask(provider, blobs, DiscoveryAssistant, AssistantInput(
        query="closing prices", unmatched_terms=["closing prices"],
        glossary=[GlossaryRef(key="close_price", name="Close price", aliases=["closing price"]),
                  GlossaryRef(key="open_price", name="Open price", aliases=[])]))


def test_profiler(provider, blobs):
    requirement = uuid.uuid4()
    items = [ItemCoverage(item="volume", kind="element", declared=True, column="qty", in_file=True, null_rate=0.0,
                          distinct_count=1),
             ItemCoverage(item="isin", kind="element", declared=False, column=None, in_file=False, null_rate=None,
                          distinct_count=None)]
    ask(provider, blobs, ProfilerAgent, ProfilerInput(
        feed_id=FEED, feed_code="syn_a", profile_id=uuid.uuid4(), outcome="profiled", row_count=1, columns=[COLUMN],
        detail=None, spec=SourceSpec(source_code="syn_a", elements=["volume"], other_fields=[],
                                     columns={"qty": "volume"}),
        coverage=[RequirementCoverage(requirement_id=requirement, items=items, missing=["isin"])],
        requirement_titles={requirement: "volume"}))


def test_glossary_builder(provider, blobs):
    columns = [NewColumn(name="rating", type="VARCHAR", null_rate=0.0, distinct_count=10,
                         candidates=candidate_list("rating", "VARCHAR", GLOSSARY))]
    ask(provider, blobs, GlossaryBuilderAgent, GlossaryBuilderInput(
        run_id=uuid.uuid4(), feed_id=FEED, source_code="syn_bond_terms", source_name="Bond terms",
        source_domain="security_master", domains=["pricing", "security_master"], columns=columns))


def test_mapping(provider, blobs):
    columns = [ColumnToMap(name="date", type="DATE", null_rate=0.0, distinct_count=10,
                           candidates=candidate_list("date", "DATE", GLOSSARY), current=None)]
    ask(provider, blobs, MappingAgent, MappingInput(
        feed_id=FEED, source_code="syn_bond_terms", source_name="Bond terms", source_description=None,
        columns=columns))


def test_rule_recommender(provider, blobs):
    offer = RuleTemplateOffer(template="present", rule_key="volume_present", description="Volume is present.",
                              sql="SELECT _row FROM curated WHERE volume IS NULL", element_keys=["volume"])
    ask(provider, blobs, RuleRecommenderAgent, RuleRecommenderInput(
        feed_id=FEED, feed_code="syn_x", source_name="X", elements=[], feed_templates=[offer], existing_keys=[],
        rejected_keys=[], mapping_ids=[], connector=None, history=[]))


def test_review_summariser(provider, blobs):
    load = uuid.uuid4()
    ask(provider, blobs, ReviewSummariserAgent, ReviewSummaryInput(
        feed_id=FEED, feed_code="syn_x", source_name="X", owner_id="dev-data-owner", licence="L", permitted_use="P",
        redistribution="R", mapped=[], unmapped_columns=["note"], rejected_glossary=[], rules=[],
        test_load={"load_id": load, "rows": 3, "stages": [], "flags": {}},
        pending={"glossary": 0, "mapping": 0, "rule": 0}, mapping_ids=[uuid.uuid4()], rule_ids=[uuid.uuid4()],
        test_load_id=load))


def test_the_call_count_stays_within_the_guard(provider):
    assert len(calls) <= MAX_CALLS
