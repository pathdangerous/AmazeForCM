import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme, workspace } from "../test/fixtures";
import Stepper from "./Stepper";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stepper(onOpen = vi.fn(), viewed = "mapping" as const) {
  const ws = workspace("mapping", "needs_decision");
  renderWithTheme(<Stepper steps={ws.steps} current={ws.current_step} viewed={viewed} onOpen={onOpen} />);
  return onOpen;
}

test("the current step carries aria-current and a state badge; the list is named", () => {
  stepper();
  expect(screen.getByRole("navigation", { name: "Onboarding steps" })).toBeTruthy();
  const current = screen.getByRole("button", { name: /Mapping/ });
  expect(current.getAttribute("aria-current")).toBe("step");
  expect(current.textContent).toContain("needs decision");
  expect(screen.getByRole("button", { name: /Profile/ }).getAttribute("aria-current")).toBeNull();
});

test("a waiting step is disabled, a done step is not", () => {
  stepper();
  expect((screen.getByRole("button", { name: /Rules/ }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: /Profile/ }) as HTMLButtonElement).disabled).toBe(false);
});

test("ArrowDown and ArrowUp move focus, Home and End jump", () => {
  stepper();
  const profile = screen.getByRole("button", { name: /Profile/ });
  profile.focus();
  fireEvent.keyDown(profile, { key: "ArrowDown" });
  expect(document.activeElement).toBe(screen.getByRole("button", { name: /Mapping/ }));
  fireEvent.keyDown(document.activeElement as Element, { key: "ArrowUp" });
  expect(document.activeElement).toBe(profile);
  fireEvent.keyDown(profile, { key: "End" });
  expect(document.activeElement).toBe(screen.getByRole("button", { name: /Mapping/ })); // the last step that can be opened
  fireEvent.keyDown(document.activeElement as Element, { key: "Home" });
  expect(document.activeElement).toBe(screen.getByRole("button", { name: /Upload/ }));
});

test("Enter opens a done step", () => {
  const onOpen = stepper();
  const glossary = screen.getByRole("button", { name: /Glossary/ });
  glossary.focus();
  fireEvent.keyDown(glossary, { key: "Enter" });
  expect(onOpen).toHaveBeenCalledWith("glossary");
});
