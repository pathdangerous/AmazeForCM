# Azure model for the development server

Owner-approved scope: branch from main, use secure Azure settings, and enable the
real provider only for the API started by scripts/dev.sh. Default pytest and
Playwright remain mock. Do not merge.

Files: .replit, scripts/dev.sh, focused launcher tests, and tests/live.
Also tests/conftest.py: block real external HTTP transports in unmarked tests
while retaining MockTransport and local test servers. This proves the default
suite cannot send Azure calls even with configured Secrets.
Remove empty Azure overrides; keep key authentication and global mock provider.
Add DEV_LLM_PROVIDER=azure_openai. The launcher defaults its API override to mock.

Verification: failing launcher tests first, then health, live smoke calls, full
mock suite and a secret-presence-only scan of git diff main. Never emit credential
values or raw live test output. Live response blobs remain in memory. Extend the
existing eight-agent smoke set to cover TableExtractorAgent and TransformationAgent,
and report aggregate calls and mean latency without model/deployment identifiers.

## Results

- Launcher RED: 2 failed; GREEN: 2 passed in 0.33s.
- Dev health: HTTP 200, llm=azure_openai, unavailable=none.
- Live suite: 12 passed, 3279 deselected in 24.28s. Ten successful agent calls,
  11 HTTP requests including a retry, mean latency 1676.1ms. The other two checks
  are the call-budget guard and the existing live-marker selection test.
- Secret-value scan passed for the diff and new files; no credential values emitted.
- Default mock suite: 3279 passed in 241.23s (0:04:01), exit 0, with live tests
  excluded and external HTTP transports blocked for unmarked tests.
- Restored main's single exposed port mapping after Replit's automatic port additions;
  the final configuration diff contains only the requested Azure environment changes.
