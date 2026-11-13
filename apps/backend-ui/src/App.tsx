import { useEffect, useState } from "react";
import { apiGet, describeError, type User } from "./api";
import GlossaryView from "./views/GlossaryView";
import StatusView from "./views/StatusView";

const TITLE = "Amaze Backend Workbench";
const STORAGE_KEY = "amaze.backend-ui.devUser";

// Views, chosen by the URL hash (#/status). No router library (plan, assumption 17).
const VIEWS = {
  status: "Status",
  glossary: "Glossary",
} as const;
type View = keyof typeof VIEWS;

function viewFromHash(): View {
  const name = window.location.hash.replace(/^#\/?/, "");
  return name in VIEWS ? (name as View) : "status";
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
  const [users, setUsers] = useState<User[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [me, setMe] = useState<User | null>(null);
  const [meError, setMeError] = useState<string | null>(null);

  useEffect(() => {
    const onHash = () => setView(viewFromHash());
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
        {view === "status" && <StatusView />}
        {view === "glossary" && <GlossaryView devUser={me ? selected : ""} />}
      </main>
      <footer>Synthetic data only.</footer>
    </>
  );
}
