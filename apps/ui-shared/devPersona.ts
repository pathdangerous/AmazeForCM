// Shared development identity selection, not an authentication mechanism.
type App = "backend" | "consumer";
type Persona = { id: string; app: App };
const STORAGE_KEY = "amaze.devUser";
export function personaLabel(user: { display_name: string; roles: string[] }, fourEyes: boolean): string {
  return fourEyes && user.roles.length ? `${user.display_name} (${user.roles[0]})` : user.display_name;
}

export function personaUsers<T extends Persona>(users: T[], fourEyes: boolean): T[] {
  return fourEyes ? users : users.filter(u => ["dev-operator", "dev-requester"].includes(u.id));
}

export function remember(id: string): void {
  try { localStorage.setItem(STORAGE_KEY, id); } catch { /* Browser storage may be disabled. */ }
}

export function initialUser(users: Persona[], app: App): string {
  const url = new URL(window.location.href);
  const transferred = url.searchParams.get("devUser");
  let saved: string | null = null;
  try { saved = localStorage.getItem(STORAGE_KEY); } catch { /* Use the app's first persona. */ }
  if (transferred !== null) {
    url.searchParams.delete("devUser");
    window.history.replaceState(null, "", url);
  }
  return users.find(u => u.id === (transferred ?? saved) && u.app === app)?.id
    ?? users.find(u => u.app === app)?.id ?? "";
}

export function personaUrl(user: Persona, href = window.location.href): string {
  const here = new URL(href);
  const path = user.app === "consumer" ? "/market/" : "/";
  if (["localhost", "127.0.0.1"].includes(here.hostname) && ["5173", "5174"].includes(here.port)) {
    here.port = user.app === "consumer" ? "5174" : "5173";
    here.pathname = path;
    here.hash = "";
    here.search = "";
    here.searchParams.set("devUser", user.id);
    return here.href;
  }
  return path;
}

export const navigation = { assign: (url: string) => window.location.assign(url) };

export function choosePersona(user: Persona, currentApp: App): boolean {
  remember(user.id);
  if (user.app === currentApp) return true;
  navigation.assign(personaUrl(user));
  return false;
}
