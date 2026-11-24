import { Anchor, NativeSelect, Title } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { apiGet, describeError, type Person, type User } from "./api";
import FeedRegistryView from "./views/FeedRegistryView";
import FeedView from "./views/FeedView";
import GlossaryView from "./views/GlossaryView";
import NotificationsView, { useInbox } from "./views/NotificationsView";
import OnboardingsView from "./views/OnboardingsView";
import OverviewView from "./views/OverviewView";
import ReportsView from "./views/ReportsView";
import RunBanner from "./views/RunBanner";
import WorkspaceView from "./views/WorkspaceView";
import StatusView from "./views/StatusView";
import UploadView from "./views/UploadView";

const TITLE = "Amaze Backend Workbench";
import { choosePersona, initialUser, personaUsers, remember, personaLabel } from "../../ui-shared/devPersona";

// Views, chosen by the URL hash (#/status). No router library (plan, assumption 17).
const VIEWS = {
  overview: "Operations overview",
  onboardings: "Onboardings",
  feeds: "Feeds",
  glossary: "Glossary",
  status: "Status",
  notifications: "Notifications",
  reports: "Reports",
} as const;
type View = keyof typeof VIEWS | "onboard" | "onboarding";

function viewFromHash(): View {
  const name = window.location.hash.replace(/^#\/?/, "");
  if (name.startsWith("feed/")) return "feeds"; // one feed's page belongs to the registry tab
  if (name.startsWith("onboarding/")) return "onboarding";
  if (name.startsWith("reports/")) return "reports"; // one report (phase 7.15)
  if (name === "onboard" || name === "upload") return "onboard";
  return name in VIEWS ? (name as View) : "overview";
}

function onboardingFromHash(): string | null {
  const match = window.location.hash.match(/^#\/onboarding\/([0-9a-f-]{36})$/);
  return match ? match[1] : null;
}

const NAV_OF: Record<View, keyof typeof VIEWS> = {
  overview: "overview",
  onboardings: "onboardings", onboard: "onboardings", onboarding: "onboardings", feeds: "feeds",
  glossary: "glossary", status: "status", notifications: "notifications", reports: "reports",
};

function reportFromHash(): string | null {
  const match = window.location.hash.match(/^#\/reports\/([0-9a-f-]{36})$/);
  return match ? match[1] : null;
}

const NAV_GROUPS: { label: string; items: (keyof typeof VIEWS)[] }[] = [
  { label: "Overview", items: ["overview"] },
  { label: "Onboard", items: ["onboardings", "feeds"] },
  { label: "Govern", items: ["glossary"] },
  { label: "Operate", items: ["status", "notifications"] },
  { label: "Masters", items: [] },
  { label: "Insights", items: ["reports"] },
];

function feedFromHash(): string | null {
  const match = window.location.hash.match(/^#\/feed\/([0-9a-f-]{36})$/);
  return match ? match[1] : null;
}

function App() {
  const [view, setView] = useState<View>(viewFromHash);
  const [feedId, setFeedId] = useState<string | null>(feedFromHash);
  const [onboardingId, setOnboardingId] = useState<string | null>(onboardingFromHash);
  const [reportId, setReportId] = useState<string | null>(reportFromHash);
  const [users, setUsers] = useState<User[]>([]);
  const [fourEyes, setFourEyes] = useState(true);
  const [selected, setSelected] = useState<string>("");
  const [me, setMe] = useState<User | null>(null);
  const [meError, setMeError] = useState<string | null>(null);
  const [people, setPeople] = useState<Record<string, string>>({}); // phase 7.5, P3 and P8: id -> display name
  const [menuOpen, setMenuOpen] = useState(() => window.innerWidth >= 768);
  const [search, setSearch] = useState("");
  const [feedSearch, setFeedSearch] = useState("");
  // phase 5: bumped when an action or a finished glossary run changes what the banner and the views show
  const [changed, setChanged] = useState(0);
  const onChange = useCallback(() => setChanged((c) => c + 1), []);
  const inbox = useInbox(me ? selected : "", view === "notifications"); // phase 7.5, P27

  useEffect(() => {
    const onHash = () => {
      setView(viewFromHash());
      setFeedId(feedFromHash());
      setOnboardingId(onboardingFromHash());
      setReportId(reportFromHash());
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    Promise.all([apiGet<User[]>("/api/dev/users"), apiGet<{ four_eyes: boolean }>("/api/health")])
      .then(([all, health]) => {
        const list = personaUsers(all, health.four_eyes);
        setFourEyes(health.four_eyes);
        setUsers(list);
        setSelected(initialUser(list, "backend"));
      })
      .catch((err: unknown) => { setUsers([]); setMeError(describeError(err)); });
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
    <div className="operator-shell">
      <header className="app-header">
        <div className="app-header-inner">
          <Title order={1} className="sr-only">{TITLE}</Title>
          <button id="operator-menu-toggle" type="button" className="menu-toggle" aria-label="Toggle menu"
            aria-expanded={menuOpen} aria-controls="operator-navigation" onClick={() => setMenuOpen((open) => !open)}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <a href="#/overview" className="brand"><strong>amaze</strong><span>for Capital Markets</span></a>
          <form role="search" className="header-search" onSubmit={(event) => {
            event.preventDefault(); setFeedSearch(search.trim()); window.location.hash = "#/feeds";
          }}>
            <label className="sr-only" htmlFor="operator-search">Search feeds</label>
            <input id="operator-search" type="search" placeholder="Search feeds…" value={search}
              onChange={(event) => {
                setSearch(event.currentTarget.value);
                if (view === "feeds" && !feedId) setFeedSearch(event.currentTarget.value);
              }} />
          </form>
          <a className="header-notifications" href="#/notifications" aria-label={`View notifications (${inbox.unseen} unseen)`}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" />
            </svg>
            {inbox.unseen > 0 && <span aria-hidden="true">{inbox.unseen}</span>}
          </a>
          <div className="who">
            <label htmlFor="dev-user"><span aria-hidden="true">Working as</span><span className="sr-only">Dev user</span></label>
            <NativeSelect id="dev-user" data-testid="dev-user" value={selected} w={240} style={{ flex: "none" }}
                          onChange={(e) => {
                            const user = users.find(u => u.id === e.currentTarget.value);
                            if (user && choosePersona(user, "backend")) setSelected(user.id);
                          }}
                          data={users.map((u) => ({ value: u.id, label: personaLabel(u, fourEyes) }))} />
            <span data-testid="signed-in-as" className="who-name" aria-label="Signed in user">
              {me ? me.display_name : meError ? <span className="who-error">{meError}</span> : ""}
            </span>
          </div>
        </div>
      </header>
      <div className="operator-layout">
      <nav id="operator-navigation" className="operator-sidebar" aria-label="Main" hidden={!menuOpen}
        onKeyDown={(event) => {
          if (event.key === "Escape") { setMenuOpen(false); document.getElementById("operator-menu-toggle")?.focus(); }
        }}>
        {NAV_GROUPS.map((group) => <div key={group.label}>
          <div className="nav-group-label">{group.label}</div>
          {group.items.map((name) => <Anchor key={name} href={`#/${name}`} aria-current={NAV_OF[view] === name ? "page" : undefined}
            onClick={() => { if (window.innerWidth < 768) setMenuOpen(false); }}>
            {VIEWS[name]}{name === "notifications" && inbox.unseen > 0 ? ` (${inbox.unseen})` : ""}
          </Anchor>)}
        </div>)}
      </nav>
      <div className="operator-content">
      <main className="app-main">
        <div className="app-panel">
        <RunBanner devUser={me ? selected : ""} refresh={`${view}/${feedId}/${changed}`} onChange={onChange}
                   canRun={!!me && (me.roles.includes("glossary_owner") || me.roles.includes("backend_team"))} />
        {view === "overview" && <OverviewView devUser={me ? selected : ""} />}
        {view === "reports" && <ReportsView devUser={me ? selected : ""} roles={me?.roles ?? []} reportId={reportId} />}
        {view === "status" && <StatusView devUser={me ? selected : ""} people={people} />}
        {view === "glossary" && (
          <GlossaryView devUser={me ? selected : ""} canDecide={!!me && me.roles.includes("glossary_owner")} people={people}
                        changed={changed} onChange={onChange} />
        )}
        {view === "notifications" && <NotificationsView rows={inbox.rows} error={inbox.error} onRefresh={inbox.refresh} />}
        {view === "onboardings" && <OnboardingsView devUser={me ? selected : ""} />}
        {view === "onboard" && (
          <UploadView me={me} onUploaded={(id, sessionId) => {
            window.location.hash = sessionId ? `#/onboarding/${sessionId}` : `#/feed/${id}`;
          }} />
        )}
        {view === "onboarding" && onboardingId && (
          <WorkspaceView id={onboardingId} devUser={me ? selected : ""} people={people} />
        )}
        {view === "feeds" && !feedId && <FeedRegistryView devUser={me ? selected : ""} people={people} me={me} search={feedSearch} />}
        {view === "feeds" && feedId && (
          <FeedView feedId={feedId} devUser={me ? selected : ""} me={me} people={people}
                    canAct={!!me && me.roles.includes("backend_team")}
                    canRun={!!me && (me.roles.includes("glossary_owner") || me.roles.includes("backend_team"))}
                    canMap={!!me && me.roles.includes("data_steward")} changed={changed} onChange={onChange} />
        )}
        </div>
      </main>
      <footer>Synthetic data only.</footer>
      </div>
      </div>
    </div>
  );
}

export default App;
