import { configure } from "@testing-library/react";

// Root cause of the flaky component checks (phase 7.12): the waits were sized for an idle machine. Testing Library's
// findBy and waitFor give up after 1 s, and vitest after 5 s a test; with 30 jsdom environments loading at once (or a
// test run beside the Python suite) a render and a stubbed fetch took longer, and tests that passed alone failed. A wait
// that is too short is not a result: these only bound a test that is really stuck. Nothing is retried or skipped.
configure({ asyncUtilTimeout: 8_000 });
