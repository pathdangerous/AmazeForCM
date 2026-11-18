import { Anchor, NativeSelect, Title } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { apiGet, describeError, type Person, type User } from "./api";
import FeedRegistryView from "./views/FeedRegistryView";
import FeedView from "./views/FeedView";
import GlossaryView from "./views/GlossaryView";
import NotificationsView from "./views/NotificationsView";
import RunBanner from "./views/RunBanner";
import StatusView from "./views/StatusView";

const TITLE = "Amaze Backend Workbench";
const STORAGE_KEY = "amaze.backend-ui.devUser";

// Views, chosen by the URL hash (#/status). No router library (plan, assumption 17).
const VIEWS = {
  status: "Status",
  glossary: "Glossary",
  feeds: "Feed registry",
  notifications: "Notifications",
} as const;
type View = keyof typeof VIEWS;

function viewFromHash(): View {
  const name = window.location.hash.replace(/^#\/?/, "");
  if (name.startsWith("feed/")) return "feeds"; // one feed's page belongs to the registry tab
  return name in VIEWS ? (name as View) : "status";
}

function feedFromHash(): string | null {
  const match = window.location.hash.match(/^#\/feed\/([0-9a-f-]{36})$/);
  return match ? match[1] : null;
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
  const [view, setView] = useState<View>(viewFromHash);
  const [feedId, setFeedId] = useState<string | null>(feedFromHash);
  const [users, setUsers] = useState<User[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [me, setMe] = useState<User | null>(null);
  const [meError, setMeError] = useState<string | null>(null);
  const [people, setPeople] = useState<Record<string, string>>({}); // phase 7.5, P3 and P8: id -> display name
  // phase 5: bumped when an action or a finished glossary run changes what the banner and the views show
  const [changed, setChanged] = useState(0);
  const onChange = useCallback(() => setChanged((c) => c + 1), []);

  useEffect(() => {
    const onHash = () => {
      setView(viewFromHash());
      setFeedId(feedFromHash());
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    apiGet<User[]>("/api/dev/users?app=backend")
      .then((list) => {
        setUsers(list);
        const saved = remembered();
        setSelected(list.some((u) => u.id === saved) ? (saved as string) : (list[0]?.id ?? ""));
      })
      .catch(() => setUsers([]));
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

  useEffect(() => {
    if (!me) return;
    apiGet<Person[]>("/api/amaze/people", me.id)
      .then((list) => setPeople(Object.fromEntries(list.map((p) => [p.id, p.display_name]))))
      .catch(() => setPeople({})); // ids then show as themselves
  }, [me]);

  return (
    <>
      <header className="app-header">
        <div className="app-header-inner">
          <Title order={1}>{TITLE}</Title>
          <nav aria-label="Views" className="app-nav">
            {(Object.keys(VIEWS) as View[]).map((name) => (
              <Anchor key={name} href={`#/${name}`} aria-current={view === name ? "page" : undefined}>
                {VIEWS[name]}
              </Anchor>
            ))}
          </nav>
          <div className="who">
            <label htmlFor="dev-user">Dev user</label>
            <NativeSelect id="dev-user" data-testid="dev-user" value={selected}
                          onChange={(e) => setSelected(e.currentTarget.value)}
                          data={users.map((u) => ({ value: u.id, label: `${u.display_name} (${u.roles.join(", ")})` }))} />
            <span data-testid="signed-in-as">
              {me ? `Signed in as ${me.display_name}` : meError ? <span className="who-error">{meError}</span> : ""}
            </span>
          </div>
        </div>
      </header>
      <main className="app-main">
        <div className="app-panel">
        <RunBanner devUser={me ? selected : ""} refresh={`${view}/${feedId}/${changed}`} onChange={onChange}
                   canRun={!!me && (me.roles.includes("glossary_owner") || me.roles.includes("backend_team"))} />
        {view === "status" && <StatusView />}
        {view === "glossary" && (
          <GlossaryView devUser={me ? selected : ""} canDecide={!!me && me.roles.includes("glossary_owner")} people={people}
                        changed={changed} onChange={onChange} />
        )}
        {view === "notifications" && <NotificationsView devUser={me ? selected : ""} />}
        {view === "feeds" && !feedId && <FeedRegistryView devUser={me ? selected : ""} />}
        {view === "feeds" && feedId && (
          <FeedView feedId={feedId} devUser={me ? selected : ""} me={me} people={people}
                    canAct={!!me && me.roles.includes("backend_team")}
                    canRun={!!me && (me.roles.includes("glossary_owner") || me.roles.includes("backend_team"))}
                    canMap={!!me && me.roles.includes("data_steward")} changed={changed} onChange={onChange} />
        )}
        </div>
      </main>
      <footer>Synthetic data only.</footer>
    </>
  );
}
