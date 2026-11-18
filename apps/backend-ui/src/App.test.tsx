import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";
import { shellRoutes, stubFetch } from "./test/fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("the dev-user select lists the stubbed users", async () => {
  stubFetch(shellRoutes());
  render(<App />);
  const select = await screen.findByTestId("dev-user");
  expect(select.tagName).toBe("SELECT"); // a native select: the story's selectOption needs it (plan, 10.3)
  await within(select).findByText("Blake Backend (backend_team)");
  expect(within(select).getByText("Sam Steward (data_steward)")).toBeTruthy();
  expect(await screen.findByTestId("signed-in-as")).toHaveProperty("textContent", "Signed in as Blake Backend");
});
