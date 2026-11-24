import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { FEED_ID, renderWithTheme, stubFetch } from "../test/fixtures";
import PipelinePanel from "./PipelinePanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const RUNS = `GET /api/amaze/feeds/${FEED_ID}/pipeline-runs`;
const RUN = `POST /api/amaze/feeds/${FEED_ID}/pipeline-runs`;
const SHA = "a".repeat(64);
const stage = (name: string, status: string, log: string[]) => ({ stage: name, status, log });
const passed = (id: string, sha = SHA) => ({
  id, created_at: "2026-11-24T10:00:00Z", feed_id: FEED_ID, code_sha256: sha, commit_hash: "c0ffee1234" + "0".repeat(30),
  commit_created: true, outcome: "passed", run_by: "dev-operator", code_version: null,
  stages: [stage("commit", "passed", ["Committed 5 files as c0ffee1234 (a new commit)."]), stage("sql_parse", "passed", ["curated.sql: 2 statements parse"]),
    stage("rule_check", "passed", ["No rules."]), stage("test_load", "passed", ["raw: 70 rows in, 70 rows out"])],
});
const failed = {
  ...passed("r0"), outcome: "failed_at_rule_check", commit_hash: "deadbeef00" + "0".repeat(30),
  stages: [stage("commit", "passed", ["Committed."]), stage("sql_parse", "passed", ["ok"]),
    stage("rule_check", "failed", ["rules/bad.sql: the sandbox refuses it: a rule is a SELECT, not DELETE"]), stage("test_load", "skipped", ["Skipped: an earlier stage failed."])],
};

test("a run is shown as a result in words, with its stages, logs and the short commit hash", async () => {
  stubFetch({ [RUNS]: [failed, passed("r1")] });
  renderWithTheme(<PipelinePanel feedId={FEED_ID} devUser="dev-operator" sha256={SHA} canRun={false} />);
  const runs = await screen.findAllByTestId("pipeline-run");
  expect(runs).toHaveLength(2);
  expect(runs[0].textContent).toContain("Failed at rule check");
  expect(runs[0].textContent).toContain("rules/bad.sql: the sandbox refuses it");
  expect(runs[0].textContent).toContain("test load: skipped");
  expect(runs[0].textContent).toContain("deadbeef00");
  expect(runs[1].textContent).toContain("Passed");
  expect(runs[1].textContent).toContain("raw: 70 rows in, 70 rows out");
  expect(screen.queryByRole("button", { name: "Run pipeline" })).toBeNull(); // an approved version is only read
});

test("only the runs of the shown code are listed", async () => {
  stubFetch({ [RUNS]: [passed("r2", "b".repeat(64)), passed("r1")] });
  renderWithTheme(<PipelinePanel feedId={FEED_ID} devUser="dev-operator" sha256={SHA} canRun={false} />);
  expect(await screen.findAllByTestId("pipeline-run")).toHaveLength(1);
});

test("Run pipeline posts, disables itself while it runs, then lists the new run first", async () => {
  const fetchStub = stubFetch({ [RUNS]: [], [RUN]: passed("r9") });
  renderWithTheme(<PipelinePanel feedId={FEED_ID} devUser="dev-operator" sha256={SHA} canRun />);
  await waitFor(() => expect(screen.getByText("This code has not been run through the pipeline.")).toBeTruthy());
  const button = screen.getByRole("button", { name: "Run pipeline" }) as HTMLButtonElement;
  fireEvent.click(button);
  expect(button.disabled).toBe(true);
  await waitFor(() => expect(screen.getAllByTestId("pipeline-run")).toHaveLength(1));
  expect(screen.getByRole("status").textContent).toContain("Passed");
  expect(fetchStub.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(true);
  expect(button.disabled).toBe(false);
});

test("a refused run shows the API's words", async () => {
  stubFetch({ [RUNS]: [], [RUN]: new (await import("../test/fixtures")).Reply(409, { detail: "The configuration changed while the pipeline ran." }) });
  renderWithTheme(<PipelinePanel feedId={FEED_ID} devUser="dev-operator" sha256={SHA} canRun />);
  await waitFor(() => screen.getByRole("button", { name: "Run pipeline" }));
  fireEvent.click(screen.getByRole("button", { name: "Run pipeline" }));
  expect((await screen.findByRole("alert")).textContent).toContain("The configuration changed while the pipeline ran.");
});
