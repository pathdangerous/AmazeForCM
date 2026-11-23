import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Component checks (phase 7.5 plan, assumption 9): npm test = vitest run, files src/**/*.test.tsx, in jsdom,
// fetch stubbed with synthetic answers (src/test/fixtures.ts). Not part of the build or the bundle.
// Run from a path with an upper-case drive letter (PowerShell always gives F:\; in Git Bash use /F/, not /f/):
// from f:\ vitest loads itself twice, once per casing, and every file fails "failed to find the current suite".
export default defineConfig({
  plugins: [react()],
  resolve: { dedupe: ["react", "react-dom", "@mantine/core", "@mantine/hooks"] },
  // testTimeout and src/test/setup.ts: the waits were sized for an idle machine (see setup.ts); a stuck test still fails.
  test: { environment: "jsdom", include: ["src/**/*.test.tsx"], setupFiles: ["src/test/setup.ts"], testTimeout: 20_000 },
});
