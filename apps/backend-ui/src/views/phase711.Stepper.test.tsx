import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme, workspace } from "../test/fixtures";
import Stepper from "./Stepper";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("horizontal stepper supports Left/Right as well as existing vertical keys, skips locked steps and never auto-advances", () => {
  const onOpen = vi.fn();
  const ws = workspace("mapping", "needs_decision");
  // A later stale step is inspectable, but waiting transformations/rules remain locked.
  ws.steps = ws.steps.map((step) => step.name === "go_live" ? { ...step, state: "stale" } : step);
  renderWithTheme(<Stepper steps={ws.steps} current="mapping" viewed="glossary" onOpen={onOpen} />);
  const mapping = screen.getByTestId("step-mapping");
  mapping.focus();
  fireEvent.keyDown(mapping, { key: "ArrowRight" });
  expect(document.activeElement).toBe(screen.getByTestId("step-go_live"));
  expect(onOpen).not.toHaveBeenCalled();
  fireEvent.keyDown(document.activeElement!, { key: "ArrowLeft" });
  expect(document.activeElement).toBe(mapping);
  fireEvent.keyDown(mapping, { key: "Home" });
  expect(document.activeElement).toBe(screen.getByTestId("step-upload"));
  fireEvent.keyDown(document.activeElement!, { key: "ArrowLeft" });
  expect(document.activeElement).toBe(screen.getByTestId("step-upload"));
  fireEvent.keyDown(document.activeElement!, { key: "End" });
  expect(document.activeElement).toBe(screen.getByTestId("step-go_live"));
  fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
  expect(document.activeElement).toBe(screen.getByTestId("step-go_live"));
  fireEvent.keyDown(document.activeElement!, { key: " " });
  expect(onOpen).toHaveBeenCalledExactlyOnceWith("go_live");
  expect(mapping.getAttribute("aria-current")).toBe("step");
  expect(screen.getByTestId("step-glossary").getAttribute("data-viewed")).toBe("true");
  expect(screen.getByTestId("step-rules")).toHaveProperty("disabled", true);
});
