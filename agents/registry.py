"""Agent registry and prompt versions (phase 3 plan, assumption 24).

AGENT_PROMPTS: each agent and the prompt id it uses.
PROMPT_VERSIONS: the current version of each prompt id; Agent.run() uses this one.
PROMPT_FILE_HASHES: every file in agents/prompts and its hash (line endings read as LF). A prompt file
is never edited: a change is a new file <id>.v<n+1>.yaml, a new hash here, and a new current version.
tests/test_prompts.py fails on any file that is missing, extra or changed.
"""

from __future__ import annotations

AGENT_PROMPTS: dict[str, str] = {
    "IntentAgent": "intent",                    # S1 scope
    "SourceDiscoveryAgent": "source_discovery",  # S2 source list
    "DiscoveryAssistant": "discovery_assistant",  # step 17, read only
    "ProfilerAgent": "profiler",  # step 02 summary (phase 4)
    "GlossaryBuilderAgent": "glossary_builder",  # step 05, in the background (phase 5)
    "MappingAgent": "mapping",  # step 06 (phase 5)
    "MappingEvaluator": "mapping",  # the gold set, read only (phase 5, assumption 37)
    "RuleRecommenderAgent": "rule_recommender",  # step 09 (phase 6)
    "ReviewSummariserAgent": "review_summariser",  # step 11 (phase 6)
}

PROMPT_VERSIONS: dict[str, str] = {
    "intent": "v1",
    "source_discovery": "v1",
    "discovery_assistant": "v1",
    "profiler": "v1",
    "glossary_builder": "v1",
    "mapping": "v1",
    "rule_recommender": "v1",
    "review_summariser": "v1",
}

PROMPT_FILE_HASHES: dict[str, str] = {
    "discovery_assistant.v1.yaml": "ad80a7a9430302ee8b84bf1eb0aaf70806be15631dd766df4ceba794156f0af0",
    "glossary_builder.v1.yaml": "b0a179a2943d24bb202fd16162492bab7e6d7b4a7f77309ad07d76929300a378",
    "mapping.v1.yaml": "9afdfcd5605b25a07a26b743f2672f340f3a3251ec87de63671d6f0bb7ed2fc4",
    "intent.v1.yaml": "40ae53c05f86abac70d7993f22e0c78bd8456252d0aaa94f10627a949f0dbb05",
    "profiler.v1.yaml": "9b0b2ab260def592ebe306abacab4c30420ce28aab4a71e3c66b1cdd52c66d51",
    "review_summariser.v1.yaml": "a0d42d5a0256cf4904367ec3abaf8289db650ad1961d07c1640d25388f2a8baa",
    "rule_recommender.v1.yaml": "24571657cbbaffc0488fba3ee1ad8e7f51c52e5a1c51063a9ce148242f3de0ae",
    "source_discovery.v1.yaml": "80fa3af7fdc9ba1429024fd0fe65bf6fbd40137bc9f8ddf91ee56b0103112d61",
}
