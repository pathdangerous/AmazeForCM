import { useCallback, useEffect, useState } from "react";
import { apiGet, describeError, type User } from "./api";
import FeedRegistryView from "./views/FeedRegistryView";
import FeedView from "./views/FeedView";
import GlossaryView from "./views/GlossaryView";
import RunBanner from "./views/RunBanner";
import StatusView from "./views/StatusView";

const TITLE = "Amaze Backend Workbench";
const STORAGE_KEY = "amaze.backend-ui.devUser";

// Views, chosen by the URL hash (#/status). No router library (plan, assumption 17).
const VIEWS = {
  status: "Status",
  glossary: "Glossary",
  feeds: "Feed registry",
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

  return (
    <>
      <header>
        <h1>{TITLE}</h1>
        <nav aria-label="Views">
          {(Object.keys(VIEWS) as View[]).map((name) => (
            <a key={name} href={`#/${name}`} aria-current={view === name ? "page" : undefined}>
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
      </header>
      <main>
        <RunBanner devUser={me ? selected : ""} refresh={`${view}/${feedId}/${changed}`} onChange={onChange}
                   canRun={!!me && (me.roles.includes("glossary_owner") || me.roles.includes("backend_team"))} />
        {view === "status" && <StatusView />}
        {view === "glossary" && (
          <GlossaryView devUser={me ? selected : ""} canDecide={!!me && me.roles.includes("glossary_owner")}
                        changed={changed} onChange={onChange} />
        )}
        {view === "feeds" && !feedId && <FeedRegistryView devUser={me ? selected : ""} />}
        {view === "feeds" && feedId && (
          <FeedView feedId={feedId} devUser={me ? selected : ""} canAct={!!me && me.roles.includes("backend_team")}
                    canRun={!!me && (me.roles.includes("glossary_owner") || me.roles.includes("backend_team"))}
                    canMap={!!me && me.roles.includes("data_steward")} changed={changed} onChange={onChange} />
        )}
      </main>
      <footer>Synthetic data only.</footer>
    </>
  );
}
