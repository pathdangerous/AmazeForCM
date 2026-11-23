import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { navigation } from "../../ui-shared/devPersona";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";
import { renderWithTheme, shellRoutes, stubFetch } from "./test/fixtures";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("the dev-user select lists the stubbed users", async () => {
  stubFetch(shellRoutes());
  renderWithTheme(<App />);
  const select = await screen.findByTestId("dev-user");
  expect(select.tagName).toBe("SELECT"); // a native select: the story's selectOption needs it (plan, 10.3)
  await within(select).findByText("Riley Requester (requester)");
  expect(within(select).getByText("Ari Analyst (requester)")).toBeTruthy();
  expect(await screen.findByTestId("signed-in-as")).toHaveProperty("textContent", "Signed in as Riley Requester");
});

test("four-eyes off exposes exactly operator and requester", async () => {
  const users = [
    { id: "dev-operator", display_name: "Operator", roles: ["backend_team"], app: "backend" },
    { id: "dev-requester", display_name: "Requester", roles: ["requester"], app: "consumer" },
    { id: "dev-analyst", display_name: "Analyst", roles: ["requester"], app: "consumer" },
  ];
  stubFetch({ ...shellRoutes(), "GET /api/dev/users": users,
    "GET /api/health": { status: "ok", database: "ok", adapters: {}, unavailable: [], four_eyes: false } });
  renderWithTheme(<App />);
  const select = await screen.findByTestId("dev-user");
  await within(select).findByText("Requester (requester)");
  expect(within(select).getAllByRole("option").map(o => (o as HTMLOptionElement).value))
    .toEqual(["dev-operator", "dev-requester"]);
  const assign = vi.spyOn(navigation, "assign").mockImplementation(() => {});
  fireEvent.change(select, { target: { value: "dev-operator" } });
  expect(assign).toHaveBeenCalledWith("/");
  expect(localStorage.getItem("amaze.devUser")).toBe("dev-operator");
});
