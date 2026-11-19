import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { onboardingRow, renderWithTheme, SESSION_ID, stubFetch } from "../test/fixtures";
import OnboardingsView from "./OnboardingsView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("a session row shows the feed, the step, the status, the progress and links to the workspace", async () => {
  stubFetch({ "GET /api/amaze/onboardings": [onboardingRow()] });
  renderWithTheme(<OnboardingsView devUser="dev-backend-team" />);
  const row = await screen.findByTestId("onboarding-row-security_master");
  expect(within(row).getByText("Security master")).toBeTruthy();
  expect(within(row).getByText("Mapping")).toBeTruthy(); // the current step as a label
  expect(within(row).getByText("active")).toBeTruthy();
  expect(within(row).getByText("4 of 9")).toBeTruthy(); // upload, profile, glossary done; mapping is the 4th
  const link = within(row).getByRole("link", { name: /Security master/ });
  expect(link.getAttribute("href")).toBe(`#/onboarding/${SESSION_ID}`);
});

test("the big button starts an onboarding", async () => {
  stubFetch({ "GET /api/amaze/onboardings": [onboardingRow()] });
  renderWithTheme(<OnboardingsView devUser="dev-backend-team" />);
  const button = await screen.findByTestId("onboard-a-file");
  expect(button.getAttribute("href")).toBe("#/onboard");
  expect(button.textContent).toBe("Onboard a file");
});

test("with no sessions it says where to start", async () => {
  stubFetch({ "GET /api/amaze/onboardings": [] });
  renderWithTheme(<OnboardingsView devUser="dev-backend-team" />);
  expect(await screen.findByText("No onboardings yet. Start with Onboard a file.")).toBeTruthy();
});

test("a failed read shows the reason", async () => {
  stubFetch({});
  renderWithTheme(<OnboardingsView devUser="dev-backend-team" />);
  expect(await screen.findByRole("alert")).toBeTruthy();
});
