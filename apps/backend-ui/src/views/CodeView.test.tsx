import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { FEED_ID, renderWithTheme, stubFetch } from "../test/fixtures";
import CodeView from "./CodeView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const BASE = `/api/amaze/feeds/${FEED_ID}/code`;
const v = (version: number, rows = 70) => ({
  version, sha256: `${version}`.repeat(64), approved_by: "dev-data-owner", created_at: "2026-11-23T10:00:00Z",
  paths: ["curated.sql", "rules/px_present.sql"], test_load_result: { status: "loaded", rows } });
const files1 = { "curated.sql": "SELECT 1;\n", "rules/px_present.sql": "SELECT _row FROM curated\n" };
const files2 = { "curated.sql": "SELECT 2;\n", "rules/px_present.sql": "SELECT _row FROM curated\n", "rules/new.sql": "SELECT 3\n" };

function routes(extra: Record<string, unknown> = {}) {
  return {
    [`GET ${BASE}`]: [v(1), v(2)],
    [`GET ${BASE}/preview`]: { sha256: "b".repeat(64), files: files2, differs_from_latest: false },
    [`GET ${BASE}/1`]: { ...v(1), files: files1 },
    [`GET ${BASE}/2`]: { ...v(2), files: files2 },
    [`GET ${BASE}/diff?from=1&to=2`]: { from_version: 1, to_version: 2, files: [
      { path: "curated.sql", change: "changed", diff: "--- a\n+++ b\n-SELECT 1;\n+SELECT 2;\n" },
      { path: "rules/new.sql", change: "added", diff: "+SELECT 3\n" }] },
    ...extra,
  };
}

test("the latest version is shown with its files; a file is read as text, read only", async () => {
  stubFetch(routes());
  renderWithTheme(<CodeView feedId={FEED_ID} devUser="dev-operator" />);
  await waitFor(() => expect(screen.getByTestId("code-meta").textContent).toContain("Version 2, approved by dev-data-owner"));
  expect((screen.getByLabelText("Code version") as HTMLSelectElement).value).toBe("2");
  await waitFor(() => expect(screen.getByTestId("code-file").textContent).toBe("SELECT 2;\n"));
  fireEvent.click(screen.getByRole("button", { name: "rules/new.sql" }));
  expect(screen.getByTestId("code-file").textContent).toBe("SELECT 3\n");
  expect(screen.queryByRole("textbox")).toBeNull(); // nothing to edit
});

test("version 1 can be picked, and compared with version 2: each changed file with its lines", async () => {
  stubFetch(routes());
  renderWithTheme(<CodeView feedId={FEED_ID} devUser="dev-operator" />);
  await waitFor(() => screen.getByTestId("code-meta"));
  fireEvent.change(screen.getByLabelText("Code version"), { target: { value: "1" } });
  await waitFor(() => expect(screen.getByTestId("code-file").textContent).toBe("SELECT 1;\n"));
  fireEvent.change(screen.getByLabelText("Compare with"), { target: { value: "2" } });
  const diff = await screen.findByTestId("code-diff");
  expect(diff.textContent).toContain("curated.sql");
  expect(diff.textContent).toContain("-SELECT 1;");
  expect(diff.textContent).toContain("+SELECT 2;");
  expect(diff.textContent).toContain("rules/new.sql");
});

test("with no version yet the configuration now is shown and says the first is stored at approval", async () => {
  stubFetch({ [`GET ${BASE}`]: [], [`GET ${BASE}/preview`]: { sha256: "c".repeat(64), files: files1, differs_from_latest: true } });
  renderWithTheme(<CodeView feedId={FEED_ID} devUser="dev-operator" />);
  await waitFor(() => expect(screen.getByText(/No code version yet/)).toBeTruthy());
  expect((screen.getByLabelText("Code version") as HTMLSelectElement).value).toBe("now");
  expect(screen.getByTestId("code-file").textContent).toBe("SELECT 1;\n");
  expect(screen.getByText("This differs from the latest approved version.")).toBeTruthy();
});
