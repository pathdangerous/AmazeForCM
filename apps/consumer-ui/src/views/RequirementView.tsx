import { useEffect, useState } from "react";
import { apiGet, apiPost, describeError, type FindSourceResponse, type RequirementPage } from "../api";

// One raised requirement: status, the requester's message, the source and its conditions, the last
// fetch, and the next action (phase 3 plan, section 10).
export default function RequirementView({ devUser, requirementId }: { devUser: string; requirementId: string }) {
  const [page, setPage] = useState<RequirementPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!devUser) return;
    apiGet<RequirementPage>(`/api/consumer/requirements/${requirementId}`, devUser)
      .then((p) => {
        setPage(p);
        setError(null);
      })
      .catch((err: unknown) => setError(describeError(err)));
  }, [devUser, requirementId]);

  async function findSource() {
    setBusy(true);
    setError(null);
    try {
      const found = await apiPost<FindSourceResponse>(`/api/consumer/requirements/${requirementId}/find-source`, {}, devUser);
      setPage({ ...found.requirement, message: found.message });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const waiting = page?.last_fetch?.outcome === "source_down";

  return (
    <section>
      <h2>Requirement</h2>
      <p><a href="#/requests">Back to My requests</a></p>
      {error && <p className="bad" role="alert" data-testid="error">{error}</p>}
      {page && (
        <>
          <div className={waiting ? "result warn" : "result"} role="status" data-testid="requirement-message">
            <strong>{page.message}</strong>
          </div>
          <dl>
            <dt>Asked for</dt>
            <dd>{page.title}</dd>
            <dt>Status</dt>
            <dd data-testid="requirement-status">{page.status.replaceAll("_", " ")}</dd>
            <dt>Elements</dt>
            <dd>{page.elements.join(", ") || "-"}</dd>
            <dt>Terms not in the glossary</dt>
            <dd>{page.terms.join(", ") || "-"}</dd>
            {page.source && (
              <>
                <dt>Source</dt>
                <dd data-testid="source">{page.source.name}, from {page.source.provider}</dd>
                <dt>Licence</dt>
                <dd>
                  {page.source.conditions.licence}. Use: {page.source.conditions.permitted_use}. Redistribution:{" "}
                  {page.source.conditions.redistribution}.
                </dd>
              </>
            )}
            {page.last_fetch && (
              <>
                <dt>Last fetch</dt>
                <dd data-testid="last-fetch" className={page.last_fetch.outcome === "fetched" ? "ok" : "warn"}>
                  {page.last_fetch.outcome === "fetched" ? "File received" : "Source did not answer"},{" "}
                  {new Date(page.last_fetch.at).toLocaleString()}
                </dd>
              </>
            )}
          </dl>
          {page.next_action === "find_source" && (
            <button type="button" data-testid="find-source" onClick={findSource} disabled={busy}>
              Find a source
            </button>
          )}
        </>
      )}
    </section>
  );
}
