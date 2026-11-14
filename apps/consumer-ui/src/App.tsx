import { useEffect, useState } from "react";
import { apiGet, describeError, type User } from "./api";
import RequestsView from "./views/RequestsView";
import RequirementView from "./views/RequirementView";
import SearchView from "./views/SearchView";
import StatusLine from "./views/StatusLine";

const TITLE = "Amaze Marketplace";
const STORAGE_KEY = "amaze.consumer-ui.devUser";

// Views, chosen by the URL hash (#/search). No router library (phase 2 plan, decision 16).
// Phase 3 adds #/requirement/<id>, reached from My requests and after a raise; it is not in the nav.
const VIEWS = { search: "Search", requests: "My requests" } as const;
type View = keyof typeof VIEWS;
type Route = { view: View } | { view: "requirement"; id: string };

function routeFromHash(): Route {
  const path = window.location.hash.replace(/^#\/?/, "");
  const requirement = /^requirement\/([0-9a-f-]{36})$/.exec(path);
  if (requirement) return { view: "requirement", id: requirement[1] };
  return { view: path in VIEWS ? (path as View) : "search" };
}

function remembered(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function remember(userId: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, userId);
  } catch {
    // storage blocked: the choice simply is not remembered
  }
}

export default function App() {
  const [route, setRoute] = useState<Route>(routeFromHash);
  const [users, setUsers] = useState<User[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [me, setMe] = useState<User | null>(null);
  const [meError, setMeError] = useState<string | null>(null);

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    apiGet<User[]>("/api/dev/users?app=consumer")
      .then((list) => {
        setUsers(list);
        const saved = remembered();
        setSelected(list.some((u) => u.id === saved) ? (saved as string) : (list[0]?.id ?? ""));
      })
      .catch((err: unknown) => {
        setUsers([]);
        setMeError(describeError(err)); // nobody can sign in: say why
      });
  }, []);

  useEffect(() => {
    if (!selected) return;
    remember(selected);
    apiGet<User>("/api/me", selected)
      .then((user) => {
        setMe(user);
        setMeError(null);
      })
      .catch((err: unknown) => {
        setMe(null);
        setMeError(describeError(err));
      });
  }, [selected]);

  const devUser = me ? selected : "";

  return (
    <>
      <header>
        <h1>{TITLE}</h1>
        <nav aria-label="Views">
          {(Object.keys(VIEWS) as View[]).map((name) => (
            <a key={name} href={`#/${name}`} aria-current={route.view === name ? "page" : undefined}>
              {VIEWS[name]}
            </a>
          ))}
        </nav>
        <div className="who">
          <label htmlFor="dev-user">Dev user</label>
          <select id="dev-user" data-testid="dev-user" value={selected} onChange={(e) => setSelected(e.target.value)}>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.display_name} ({u.roles.join(", ")})
              </option>
            ))}
          </select>
          <span data-testid="signed-in-as">
            {me ? `Signed in as ${me.display_name}` : meError ? <span className="bad">{meError}</span> : ""}
          </span>
        </div>
        <StatusLine />
      </header>
      <main>
        {route.view === "search" && <SearchView devUser={devUser} />}
        {route.view === "requests" && <RequestsView key={devUser} devUser={devUser} />}
        {route.view === "requirement" && (
          <RequirementView key={`${devUser}-${route.id}`} devUser={devUser} requirementId={route.id} />
        )}
      </main>
      <footer>Synthetic data only.</footer>
    </>
  );
}
