import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";
import { renderWithTheme, shellRoutes, stubFetch } from "./test/fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("the dev-user select lists the stubbed users", async () => {
  stubFetch(shellRoutes());
  renderWithTheme(<App />);
  const select = await screen.findByTestId("dev-user");
  expect(select.tagName).toBe("SELECT"); // a native select: the story's selectOption needs it (plan, 10.3)
  await within(select).findByText("Blake Backend (backend_team)");
  expect(within(select).getByText("Sam Steward (data_steward)")).toBeTruthy();
  const who = await screen.findByTestId("signed-in-as");
  expect(who).toHaveProperty("textContent", "Blake Backend"); // the name only, no "Signed in as" text
  expect(who.getAttribute("aria-label")).toBe("Signed in user");
});
