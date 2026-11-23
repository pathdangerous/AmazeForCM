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

Pending verification.
