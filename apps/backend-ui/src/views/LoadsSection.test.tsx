import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { feedPage, renderWithTheme, scheduledLoad } from "../test/fixtures";
import LoadsSection from "./LoadsSection";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const show = (load = scheduledLoad()) => renderWithTheme(
  <LoadsSection page={feedPage({ loads: [load] })} devUser="dev-operator" roles={["operations"]} onChange={vi.fn()}
                onMessage={vi.fn()} onError={vi.fn()} />);

test("the certification shows waived rows apart from flagged ones (7.14)", () => {
  show(scheduledLoad({ certification: { rows: 10, flagged_rows: 1, waived_rows: 2, waived_counts: { r: 2 },
    trust_score: "90.0", threshold: "90.0", outcome: "released", warned: [] } }));
  const text = screen.getByTestId("certification").textContent ?? "";
  expect(text).toContain("1 of 10 rows flagged");
  expect(text).toContain("2 more waived (counted as unflagged)");
});

test("a load without waived rows says nothing about waivers", () => {
  show();
  expect(screen.getByTestId("certification").textContent).not.toContain("waived");
});

test("a rerun says which load it reruns and why", () => {
  show(scheduledLoad({ rerun_of_load_id: "00000000-0000-4000-8000-0000000000aa", rerun_reason: "Waived the volumes." }));
  expect(screen.getByTestId("rerun-of").textContent).toContain("Rerun of load 00000000");
  expect(screen.getByTestId("rerun-of").textContent).toContain("Waived the volumes.");
});
