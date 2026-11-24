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
    "ExceptionFixAgent": "exception_fix",  # one fix per exception group of a held load (phase 7.14)
    "ReportSummariserAgent": "report_summariser",  # the narrative of a report; read only (phase 7.15)
}

PROMPT_VERSIONS: dict[str, str] = {
    "intent": "v1",
    "source_discovery": "v1",
    "discovery_assistant": "v1",
    "profiler": "v3",  # v3 (phase 7.12, T1): glossary fit and actions; v2 stays on disk
    "glossary_builder": "v3",  # v3 (7.12): profile_actions; v2 (7.8): units, plain names, optional samples; v1, v2 stay
    "mapping": "v2",  # v2 (7.12): profile_actions; v1 stays on disk
    "rule_recommender": "v2",  # v2 (7.12): profile_actions; v1 stays on disk
    "review_summariser": "v2",  # v2 (phase 7.8, S8): the input has no visibility; v1 stays on disk
    "table_extractor": "v1",
    "transformation": "v2",  # v2 (7.12): profile_actions; v1 stays on disk
    "exception_fix": "v1",  # phase 7.14
    "report_summariser": "v1",  # phase 7.15
}

PROMPT_FILE_HASHES: dict[str, str] = {
    "discovery_assistant.v1.yaml": "ad80a7a9430302ee8b84bf1eb0aaf70806be15631dd766df4ceba794156f0af0",
    "glossary_builder.v1.yaml": "b0a179a2943d24bb202fd16162492bab7e6d7b4a7f77309ad07d76929300a378",
    "glossary_builder.v2.yaml": "52b331eb8e82b7c01c6d75a5d2b2223febb68a666fe867167245c32a04f01e6a",
    "glossary_builder.v3.yaml": "e38e716d282963d94736b1e28173b52630a79b65b7856cdd5ab5b8a4a6e2604f",
    "mapping.v2.yaml": "c773134418cc57fd24e2a01b00548627cb9ea930c0b4ef627e1f2514887865b2",
    "transformation.v2.yaml": "f68866da61b5f2d318ab3fe6f67a52d5f48185be7daa64fe82473237df75fbd7",
    "rule_recommender.v2.yaml": "9a628abd04386e7582b5ffd9ce68607f469bc8ac6f47e3c73055aa842e8b8a93",
    "mapping.v1.yaml": "9afdfcd5605b25a07a26b743f2672f340f3a3251ec87de63671d6f0bb7ed2fc4",
    "intent.v1.yaml": "40ae53c05f86abac70d7993f22e0c78bd8456252d0aaa94f10627a949f0dbb05",
    "profiler.v1.yaml": "9b0b2ab260def592ebe306abacab4c30420ce28aab4a71e3c66b1cdd52c66d51",
    "profiler.v2.yaml": "aa0dbdd52cb241bc28cccc219d8a61f53ca06d0e0137969eed7970104a898f6f",
    "profiler.v3.yaml": "47989e0a94ee8714b61b69f58f7f95043334e415703ae004165142c9f6c3232e",
    "review_summariser.v1.yaml": "a0d42d5a0256cf4904367ec3abaf8289db650ad1961d07c1640d25388f2a8baa",
    "review_summariser.v2.yaml": "45599558401c5e5f968d9faa074667b49ff6d7043f12dd949e6391c1170887b4",
    "rule_recommender.v1.yaml": "24571657cbbaffc0488fba3ee1ad8e7f51c52e5a1c51063a9ce148242f3de0ae",
    "source_discovery.v1.yaml": "80fa3af7fdc9ba1429024fd0fe65bf6fbd40137bc9f8ddf91ee56b0103112d61",
    "table_extractor.v1.yaml": "5594c4ae2926d8a97afbf4c9688b159fe3ac2ec8fadbd45394770ec0927a8d39",
    "transformation.v1.yaml": "237793a0e219afe0b914c5e509477392ca44ffecb6b6c1028e9557b760d84c1e",
    "exception_fix.v1.yaml": "a1d963e6de239ab662f185cdf702f27d10603270b52d4103bd09475db3bcdbec",
    "report_summariser.v1.yaml": "06b54209fb4d43a9729f5c49b049055199362c25af1f5b8ff7c315a32dc2c99e",
}
