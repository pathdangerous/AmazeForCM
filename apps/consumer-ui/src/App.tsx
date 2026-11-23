import { Anchor, NativeSelect, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { apiGet, describeError, type User } from "./api";
import NotificationsView, { useInbox } from "./views/NotificationsView";
import CatalogueView from "./views/CatalogueView";
import ProductView from "./views/ProductView";
import RequestsView from "./views/RequestsView";
import RequirementView from "./views/RequirementView";
import SearchView from "./views/SearchView";
import StatusLine from "./views/StatusLine";

const TITLE = "Amaze Marketplace";
import { choosePersona, initialUser, personaUsers, remember, personaLabel } from "../../ui-shared/devPersona";

// Views, chosen by the URL hash (#/search). No router library (phase 2 plan, decision 16).
// Phase 3 adds #/requirement/<id>, reached from My requests and after a raise; it is not in the nav.
const VIEWS = { search: "Search", catalogue: "Catalogue", requests: "My requests",
                notifications: "Notifications" } as const;
type View = keyof typeof VIEWS;
type Route = { view: View } | { view: "requirement"; id: string } | { view: "product"; code: string };

function routeFromHash(): Route {
  const path = window.location.hash.replace(/^#\/?/, "");
  const product = /^product\/([A-Za-z0-9_.-]+)$/.exec(path);
  if (product) return { view: "product", code: product[1] };
  const requirement = /^requirement\/([0-9a-f-]{36})$/.exec(path);
  if (requirement) return { view: "requirement", id: requirement[1] };
  return { view: path in VIEWS ? (path as View) : "search" };
}

function App() {
  const [route, setRoute] = useState<Route>(routeFromHash);
  const [users, setUsers] = useState<User[]>([]);
  const [fourEyes, setFourEyes] = useState(true);
  const [selected, setSelected] = useState<string>("");
  const [me, setMe] = useState<User | null>(null);
  const [meError, setMeError] = useState<string | null>(null);

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    Promise.all([apiGet<User[]>("/api/dev/users"), apiGet<{ four_eyes: boolean }>("/api/health")])
      .then(([all, health]) => {
        const list = personaUsers(all, health.four_eyes);
        setFourEyes(health.four_eyes);
        setUsers(list);
        setSelected(initialUser(list, "consumer"));
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
  const inbox = useInbox(devUser, route.view === "notifications"); // phase 7.5, P27

  return (
    <div className="market-shell">
      <header className="app-header">
        <div className="app-header-inner">
          <Title order={1} className="sr-only">{TITLE}</Title>
          <a href="#/catalogue" className="brand"><strong>amaze</strong><span>Marketplace</span></a>
          <nav aria-label="Marketplace" className="app-nav">
            {(Object.keys(VIEWS) as View[]).map((name) => (
              <Anchor key={name} href={`#/${name}`} aria-current={route.view === name || route.view === "product" && name === "catalogue" || route.view === "requirement" && name === "requests" ? "page" : undefined}>
                {VIEWS[name]}{name === "notifications" && inbox.unseen > 0 ? ` (${inbox.unseen})` : ""}
              </Anchor>
            ))}
          </nav>
          <div className="who">
            <label htmlFor="dev-user"><span aria-hidden="true">Working as</span><span className="sr-only">Dev user</span></label>
            <NativeSelect id="dev-user" data-testid="dev-user" value={selected} w={240} style={{ flex: "none" }}
                          onChange={(e) => {
                            const user = users.find(u => u.id === e.currentTarget.value);
                            if (user && choosePersona(user, "consumer")) setSelected(user.id);
                          }}
                          data={users.map((u) => ({ value: u.id, label: personaLabel(u, fourEyes) }))} />
            <span data-testid="signed-in-as" className="who-name">
              {me ? `Signed in as ${me.display_name}` : meError ? <span className="who-error">{meError}</span> : ""}
            </span>
          </div>
        </div>
      </header>
      {/* below the header bar, on a panel: its ok/bad colours are not legible on the navy bar (phase 7.5) */}
      <div className="app-status"><StatusLine /></div>
      <main className="app-main">
        <div className="app-panel">
        {route.view === "search" && <SearchView devUser={devUser} />}
        {route.view === "catalogue" && <CatalogueView key={devUser} devUser={devUser} />}
        {route.view === "product" && <ProductView key={`${devUser}-${route.code}`} devUser={devUser} code={route.code} />}
        {route.view === "requests" &&<RequestsView key={devUser} devUser={devUser} />}
        {route.view === "notifications" && (
          <NotificationsView rows={inbox.rows} error={inbox.error} onRefresh={inbox.refresh} />
        )}
        {route.view === "requirement" && (
          <RequirementView key={`${devUser}-${route.id}`} devUser={devUser} requirementId={route.id} />
        )}
        </div>
      </main>
      <footer>Synthetic data only.</footer>
    </div>
  );
}

export default App;
