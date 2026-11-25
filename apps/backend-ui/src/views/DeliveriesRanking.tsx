import { useEffect, useState } from "react";
import { SectionCard } from "../../../ui-shared/phase711";
import { apiGet, describeError, type DeliveriesOverviewT } from "../api";

// Phase 7.21, T4: on the operations overview, the recurring feeds by on-time share over the last 30 days, worst first. The same
// figures as each feed's Deliveries tab (one function of the API computes both).
export default function DeliveriesRanking({ devUser }: { devUser: string }) {
  const [data, setData] = useState<DeliveriesOverviewT | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    apiGet<DeliveriesOverviewT>("/api/amaze/deliveries/overview", devUser)
      .then((d) => current && (setData(d), setError(null)))
      .catch((err: unknown) => current && setError(describeError(err)));
    return () => { current = false; };
  }, [devUser]);

  return (
    <SectionCard title="Deliveries, last 30 days">
      <div data-testid="overview-deliveries">
        {error && <p className="overview-note" role="status">Delivery figures are not available: {error}</p>}
        {data && data.feeds.length === 0 && <p className="overview-note">No recurring feed has a delivery due yet.</p>}
        {data && data.feeds.length > 0 && (
          <table data-testid="overview-deliveries-table" style={{ borderCollapse: "collapse", width: "100%" }}>
            <thead>
              <tr><th align="left">Feed</th><th align="left">Schedule</th><th align="right">On time</th><th align="right">Late</th>
                <th align="right">Missing</th><th align="right">Streak</th></tr>
            </thead>
            <tbody>
              {data.feeds.map((f) => (
                <tr key={f.feed_id} data-testid="overview-deliveries-row">
                  <td><a href={`#/feed/${f.feed_id}`}><strong>{f.code}</strong></a></td>
                  <td>{f.schedule}</td>
                  <td align="right">{f.on_time_pct === null ? "none due yet" : `${f.on_time_pct}% (${f.on_time} of ${f.expected})`}</td>
                  <td align="right">{f.late}</td>
                  <td align="right">{f.missing}</td>
                  <td align="right">{f.streak}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && (data.one_time > 0 || data.not_set > 0) && (
          <p className="overview-note" data-testid="overview-deliveries-rest">
            {data.one_time > 0 && `${data.one_time} one-time load${data.one_time === 1 ? "" : "s"} (never late or missing). `}
            {data.not_set > 0 && `${data.not_set} live feed${data.not_set === 1 ? " has" : "s have"} no schedule yet: Schedule not set.`}
          </p>
        )}
      </div>
    </SectionCard>
  );
}
