import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderWithTheme, stubFetch } from "../test/fixtures";
import SettingsView from "./SettingsView";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const AGENTS = [
  { name: "MappingAgent", autonomy: "A2", approver_roles: ["data_steward"], provider: "mock", external: false },
  { name: "ProfilerAgent", autonomy: "A2", approver_roles: ["backend_team"], provider: "mock", external: false },
];
const BASE = { llm_provider: "mock", external_agent_provider: "none", external_agent_for: "", external_agent_set: false,
  external_agent_known: false, external_agent_active: false, agents: AGENTS };

test("phase 7.18: Settings lists each agent with its provider and says EXTERNAL_AGENT_FOR is not set", async () => {
  stubFetch({ "GET /api/amaze/settings": BASE });
  renderWithTheme(<SettingsView devUser="dev-operator" />);
  expect(screen.getByRole("heading", { name: "Settings" }).className).toContain("mantine-Title");
  const row = await screen.findByTestId("agent-MappingAgent");
  expect(row.textContent).toContain("A2");
  expect(row.textContent).toContain("data steward");
  expect(row.textContent).toContain("mock");
  expect(screen.getByTestId("agent-ProfilerAgent").textContent).toContain("mock");
  expect(screen.getByTestId("external-agent").textContent).toContain("EXTERNAL_AGENT_FOR is not set");
});

test("phase 7.18: a routed agent shows external, and the page names it", async () => {
  stubFetch({ "GET /api/amaze/settings": { ...BASE, external_agent_provider: "hexaware", external_agent_for: "MappingAgent",
    external_agent_set: true, external_agent_known: true, external_agent_active: true,
    agents: [{ ...AGENTS[0], provider: "external", external: true }, AGENTS[1]] } });
  renderWithTheme(<SettingsView devUser="dev-operator" />);
  expect((await screen.findByTestId("agent-MappingAgent")).textContent).toContain("external");
  expect(screen.getByTestId("agent-ProfilerAgent").textContent).toContain("mock");
  expect(screen.getByTestId("external-agent").textContent).toContain("MappingAgent");
  expect(screen.getByTestId("external-agent").textContent).toContain("hexaware");
});

test("phase 7.18: a name set while the provider is none, or a name no agent has, is flagged", async () => {
  stubFetch({ "GET /api/amaze/settings": { ...BASE, external_agent_for: "MappingAgent", external_agent_set: true,
    external_agent_known: true } });
  renderWithTheme(<SettingsView devUser="dev-operator" />);
  expect((await screen.findByTestId("external-agent")).textContent).toContain("not routed");
  cleanup();
  stubFetch({ "GET /api/amaze/settings": { ...BASE, external_agent_provider: "hexaware", external_agent_for: "Nope",
    external_agent_set: true } });
  renderWithTheme(<SettingsView devUser="dev-operator" />);
  expect((await screen.findByTestId("external-agent")).textContent).toContain("no agent with that name");
});

test("phase 7.18: Settings is read only: no button, no input", async () => {
  stubFetch({ "GET /api/amaze/settings": BASE });
  renderWithTheme(<SettingsView devUser="dev-operator" />);
  await screen.findByTestId("agent-MappingAgent");
  expect(screen.queryAllByRole("button")).toHaveLength(0);
  expect(screen.queryAllByRole("textbox")).toHaveLength(0);
});
