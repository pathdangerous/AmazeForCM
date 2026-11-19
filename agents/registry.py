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
    "TableExtractorAgent": "table_extractor",  # direct file intake, scanned PDFs (phase 7.8)
    "TransformationAgent": "transformation",  # step 10, safe cleaning steps for the mapped columns (phase 7.8)
}

PROMPT_VERSIONS: dict[str, str] = {
    "intent": "v1",
    "source_discovery": "v1",
    "discovery_assistant": "v1",
    "profiler": "v2",
    "glossary_builder": "v2",  # v2 (phase 7.8): units, plain names, optional samples; v1 stays on disk
    "mapping": "v1",
    "rule_recommender": "v1",
    "review_summariser": "v2",  # v2 (phase 7.8, S8): the input has no visibility; v1 stays on disk
    "table_extractor": "v1",
    "transformation": "v1",
}

PROMPT_FILE_HASHES: dict[str, str] = {
    "discovery_assistant.v1.yaml": "ad80a7a9430302ee8b84bf1eb0aaf70806be15631dd766df4ceba794156f0af0",
    "glossary_builder.v1.yaml": "b0a179a2943d24bb202fd16162492bab7e6d7b4a7f77309ad07d76929300a378",
    "glossary_builder.v2.yaml": "52b331eb8e82b7c01c6d75a5d2b2223febb68a666fe867167245c32a04f01e6a",
    "mapping.v1.yaml": "9afdfcd5605b25a07a26b743f2672f340f3a3251ec87de63671d6f0bb7ed2fc4",
    "intent.v1.yaml": "40ae53c05f86abac70d7993f22e0c78bd8456252d0aaa94f10627a949f0dbb05",
    "profiler.v1.yaml": "9b0b2ab260def592ebe306abacab4c30420ce28aab4a71e3c66b1cdd52c66d51",
    "profiler.v2.yaml": "aa0dbdd52cb241bc28cccc219d8a61f53ca06d0e0137969eed7970104a898f6f",
    "review_summariser.v1.yaml": "a0d42d5a0256cf4904367ec3abaf8289db650ad1961d07c1640d25388f2a8baa",
    "review_summariser.v2.yaml": "45599558401c5e5f968d9faa074667b49ff6d7043f12dd949e6391c1170887b4",
    "rule_recommender.v1.yaml": "24571657cbbaffc0488fba3ee1ad8e7f51c52e5a1c51063a9ce148242f3de0ae",
    "source_discovery.v1.yaml": "80fa3af7fdc9ba1429024fd0fe65bf6fbd40137bc9f8ddf91ee56b0103112d61",
    "table_extractor.v1.yaml": "5594c4ae2926d8a97afbf4c9688b159fe3ac2ec8fadbd45394770ec0927a8d39",
    "transformation.v1.yaml": "237793a0e219afe0b914c5e509477392ca44ffecb6b6c1028e9557b760d84c1e",
}
