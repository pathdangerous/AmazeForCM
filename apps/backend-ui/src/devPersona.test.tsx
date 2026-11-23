import { afterEach, expect, test, vi } from "vitest";
import { choosePersona, initialUser, navigation, personaUrl, personaUsers, remember, personaLabel } from "../../ui-shared/devPersona";

const operator = { id: "dev-operator", app: "backend" as const };
const requester = { id: "dev-requester", app: "consumer" as const };
const steward = { id: "dev-data-steward", app: "backend" as const };
const users = [steward, requester, operator];
test("picker labels use only the name when off and only the first role when on", () => {
  const user = { display_name: "Olive Operator", roles: ["backend_team", "data_owner"] };
  expect(personaLabel(user, false)).toBe("Olive Operator");
  expect(personaLabel(user, true)).toBe("Olive Operator (backend_team)");
  expect(personaLabel({ display_name: "Riley Requester", roles: ["requester"] }, false)).toBe("Riley Requester");
});
afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

test("four-eyes off shows exactly the two personas; on retains all users", () => {
  expect(personaUsers(users, false).map(u => u.id)).toEqual(["dev-requester", "dev-operator"]);
  expect(personaUsers(users, true)).toEqual(users);
});

test("one choice is shared; invalid and other-app saved choices fall back safely", () => {
  remember(operator.id);
  expect(initialUser(users, "backend")).toBe(operator.id);
  expect(initialUser(users, "consumer")).toBe(requester.id);
  remember("unknown");
  expect(initialUser(users, "backend")).toBe(steward.id);
});

test("proxy navigation uses paths; local ports transfer the selected user", () => {
  expect(personaUrl(requester, "https://preview.test/#/feeds")).toBe("/market/");
  expect(personaUrl(operator, "https://preview.test/market/")).toBe("/");
  expect(personaUrl(requester, "http://127.0.0.1:5173/")).toBe("http://127.0.0.1:5174/market/?devUser=dev-requester");
  expect(personaUrl(operator, "http://localhost:5174/market/")).toBe("http://localhost:5173/?devUser=dev-operator");
});

test("switching apps stores the choice before navigating; same app stays put", () => {
  const assign = vi.spyOn(navigation, "assign").mockImplementation(() => {});
  expect(choosePersona(requester, "backend")).toBe(false);
  expect(localStorage.getItem("amaze.devUser")).toBe(requester.id);
  expect(assign).toHaveBeenCalledOnce();
  expect(choosePersona(operator, "backend")).toBe(true);
  expect(assign).toHaveBeenCalledOnce();
});

test("a transferred persona is validated and removed from the address", () => {
  window.history.replaceState(null, "", "/?devUser=dev-operator#/onboardings");
  expect(initialUser(users, "backend")).toBe("dev-operator");
  expect(window.location.search).toBe("");
  expect(window.location.hash).toBe("#/onboardings");
  window.history.replaceState(null, "", "/?devUser=untrusted");
  expect(initialUser(users, "backend")).toBe(steward.id);
  window.history.replaceState(null, "", "/");
});
