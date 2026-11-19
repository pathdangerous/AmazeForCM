# Recorded Azure OpenAI responses (synthetic)

These files are hand-made, synthetic chat-completion and error bodies in the shape Azure OpenAI returns. They are
not real model output and name no real feed, vendor or client. `tests/test_port_llm_azure.py` serves them through an
`httpx2.MockTransport`, so no test touches the network.

| File | Case |
|---|---|
| `ok.json` | a valid `ProfilerOutput` |
| `finish_content_filter.json` | 200 with `finish_reason: content_filter` |
| `refusal.json` | 200 with a `refusal` message and no content |
| `malformed.json` | 200 whose content is not JSON |
| `schema_mismatch.json` | 200 whose content is JSON of the wrong shape |
| `length.json` | 200 cut off with `finish_reason: length` |
| `error_400_content_filter.json` | 400, `error.code` is `content_filter` |
| `error_429.json` | 429 rate limit |
| `error_500.json` | 500 server error |
