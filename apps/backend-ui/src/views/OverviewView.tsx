import { useState } from "react";
import DataGrid, { type DataGridColumn } from "../../../ui-shared/DataGrid";
import { ConfidenceBar, CssDonut, KpiCard, LoadingSkeleton, SectionCard, StatusChip } from "../../../ui-shared/phase711";
import type { OperationsOverview } from "../api";
import { useOverview } from "../hooks/use-overview";
import { formatWhen } from "../theme";

type Load = OperationsOverview["recent_loads"][number];
type Pending = OperationsOverview["pending_decisions"][number];

function exportLoads(rows: Load[]) {
  const keys: (keyof Load)[] = ["feed_code", "source_code", "received_at", "purpose", "rows", "flagged_rows", "trust_score", "status"];
  const quote = (value: unknown) => {
    const raw = String(value ?? "");
    // Prevent spreadsheet formula execution when real feed/source names begin with operators.
    const safe = /^[=+\-@\t\r]/.test(raw) ? `'${raw}` : raw;
    return `"${safe.replaceAll('"', '""')}"`;
  };
  const csv = [keys.join(","), ...rows.map((row) => keys.map((key) => quote(row[key])).join(","))].join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url; link.download = "amaze-recent-loads.csv"; document.body.appendChild(link);
  link.click(); link.remove(); URL.revokeObjectURL(url);
}

export default function OverviewView({ devUser }: { devUser: string }) {
  const [days, setDays] = useState<1 | 7 | 30>(7);
  const [columnsOpen, setColumnsOpen] = useState(false);
  const [visibleColumns, setVisibleColumns] = useState(["feed", "source", "arrived", "purpose", "rows", "flagged", "score", "status"]);
  const { data, error, retry } = useOverview(devUser, days);
  const pendingColumns: DataGridColumn<Pending>[] = [
    { key: "feed", label: "Feed", value: (row) => row.feed_code, render: (row) => <strong className="decision-summary" title={`${row.feed_code} · ${row.feed_name}`}>{row.feed_code}</strong> },
    { key: "kind", label: "Proposal type", value: (row) => row.kind.replaceAll("_", " ") },
    { key: "summary", label: "Proposal", value: (row) => row.summary, render: (row) => <span className="decision-summary" title={row.summary}>{row.summary}</span> },
    { key: "agent", label: "Agent", value: (row) => row.agent },
    { key: "confidence", label: "Confidence", value: (row) => row.confidence, render: (row) => <ConfidenceBar value={row.confidence} label={`Confidence for ${row.feed_code}`} /> },
    { key: "review", label: "Review", value: () => "", sortable: false, filterable: false,
      render: (row) => <a className="amz-button" href={row.session_id ? `#/onboarding/${row.session_id}` : `#/feed/${row.feed_id}`}>Review</a> },
  ];
  const loadColumns: DataGridColumn<Load>[] = [
    { key: "feed", label: "Feed", value: (row) => row.feed_code, render: (row) => <a href={`#/feed/${row.feed_id}`}><strong>{row.feed_code}</strong></a> },
    { key: "source", label: "Source", value: (row) => row.source_code },
    { key: "arrived", label: "Arrived", value: (row) => row.received_at, render: (row) => formatWhen(row.received_at) },
    { key: "purpose", label: "Purpose", value: (row) => row.purpose.replaceAll("_", " ") },
    { key: "rows", label: "Rows", value: (row) => row.rows, numeric: true },
    { key: "flagged", label: "Flagged", value: (row) => row.flagged_rows, numeric: true },
    { key: "score", label: "Trust score", value: (row) => row.trust_score, numeric: true },
    { key: "status", label: "Status", value: (row) => row.status, render: (row) => <StatusChip
      tone={row.status === "released" ? "good" : /held|failed|quarantin/.test(row.status) ? "attention" : "neutral"}>{row.status.replaceAll("_", " ")}</StatusChip> },
  ];
  return <section className="overview-page">
    <div className="page-heading">
      <div><div className="breadcrumbs">Home / Overview</div><h1>Operations overview</h1></div>
      <div className="page-actions">
        <div role="group" aria-label="View" data-testid="overview-view">
          <button type="button" className="amz-button" aria-pressed={days === 1} onClick={() => setDays(1)}>Daily</button>
          <button type="button" className="amz-button" aria-pressed={days === 30} onClick={() => setDays(30)}>Monthly</button>
        </div>
        <label>Period<select value={days} onChange={(event) => setDays(Number(event.currentTarget.value) as 1 | 7 | 30)}>
          <option value={7}>Last 7 days</option><option value={1}>Last 24 hours</option><option value={30}>Last 30 days</option>
        </select></label>
        <a href="#/onboard" className="amz-button amz-button-primary">+ Onboard a file</a>
      </div>
    </div>
    {error ? <div className="amz-error" role="alert"><h2>Overview could not be loaded</h2><p>{error}</p>
      <button className="amz-button" onClick={retry}>Try again</button></div>
      : !data ? <LoadingSkeleton label="Loading operations overview" /> : <>
        <div className="kpi-grid">
          <KpiCard label="Feeds live" value={data.feeds_live} detail={`${data.feeds_paused} paused`} />
          <KpiCard label="All loads in period" value={data.loads_count} detail="Scheduled and go-live test loads" />
          <KpiCard label="Active onboardings" value={data.onboardings_active} detail="Onboarding sessions in progress" />
          {data.average_trust_score !== null && <KpiCard label="Average trust score" value={data.average_trust_score.toFixed(1)}
            detail={`Certified loads only · release threshold ${data.release_threshold}`} />}
        </div>
        <div className="overview-middle">
          <SectionCard title="Waiting for your decision" bare tinted
            actions={<><StatusChip>{data.pending_decisions.length}</StatusChip><span className="overview-note">Agents propose · you decide · rules execute</span></>}>
            <DataGrid rows={data.pending_decisions} columns={pendingColumns} rowKey={(row) => row.proposal_id}
              pageSize={5} filters={false} className="amz-grid decision-grid" aria-label="Waiting for your decision" emptyMessage="No proposals are waiting for your decision." />
          </SectionCard>
          {data.quality && <SectionCard title="Data quality" className="quality-card">
            <div className="quality-content">
              <CssDonut value={data.quality.pass_percent} label="Rows passing all rules" />
              <div className="quality-legend">
                <div><span className="quality-key" style={{ background: "#2563EB" }} />Pass · {data.quality.passing_rows} rows</div>
                <div><span className="quality-key" style={{ background: "#EA580C" }} />Flagged · {data.quality.flagged_rows} rows</div>
              </div>
            </div>
            <p className="overview-note">Certified loads in the selected period</p>
            {data.quality.by_feed.length > 0 && <strong style={{ fontSize: 13 }}>Flagged rows by feed</strong>}
            {data.quality.by_feed.map((feed) => <div className="quality-feed" key={feed.feed_id}>
              <div className="quality-feed-label"><a href={`#/feed/${feed.feed_id}`}>{feed.feed_code}</a><span>{feed.flagged_rows}</span></div>
              <div className="quality-track"><div style={{ width: `${data.quality!.flagged_rows ? feed.flagged_rows / data.quality!.flagged_rows * 100 : 0}%` }} /></div>
            </div>)}
          </SectionCard>}
        </div>
        {data.actions && <SectionCard title={days === 1 ? "Drift and actions today" : days === 30 ? "Drift and actions this month" : "Drift and actions"}>
          <div data-testid="overview-actions">
            <p>{`Actions selected in the period: ${data.actions.by_agent} applied by agents, ${data.actions.by_hand} done by hand; ${data.actions.pending} waiting for a decision.`}</p>
            {(data.drift_events ?? []).length === 0 ? <p className="overview-note">No delivery drifted in this period.</p>
              : <ul data-testid="overview-drift">{(data.drift_events ?? []).map((e) => <li key={e.load_id}>
                <a href={`#/feed/${e.feed_id}`}>{e.feed_code}</a>{` ${formatWhen(e.at)}: ${e.new_columns} new, ${e.missing_columns} missing, ${e.type_changes} type changes${e.order_changed ? ", order changed" : ""}`}
              </li>)}</ul>}
          </div>
        </SectionCard>}
        <SectionCard title="Recent loads" bare actions={<div className="page-actions">
          <span className="overview-note">All loads in period · scheduled and go-live tests</span>
          <div className="column-chooser">
            <button className="amz-button" aria-expanded={columnsOpen} aria-controls="load-columns" onClick={() => setColumnsOpen((open) => !open)}>Columns</button>
            {columnsOpen && <fieldset id="load-columns" className="column-chooser-options"><legend>Visible load columns</legend>
              {loadColumns.map((column) => <label key={column.key}>
                <input type="checkbox" checked={visibleColumns.includes(column.key)}
                  disabled={visibleColumns.length === 1 && visibleColumns.includes(column.key)}
                  onChange={(event) => {
                    const checked = event.currentTarget.checked;
                    setVisibleColumns((current) => checked ? [...current, column.key] : current.filter((key) => key !== column.key));
                  }} />
                {column.label}
              </label>)}
            </fieldset>}
          </div>
          <button className="amz-button" disabled={!data.recent_loads.length} onClick={() => exportLoads(data.recent_loads)}>Export CSV</button>
        </div>}>
          <DataGrid rows={data.recent_loads} columns={loadColumns.filter((column) => visibleColumns.includes(column.key))} rowKey={(row) => row.id} pageSize={5}
            aria-label="Recent loads" emptyMessage="No loads in this period." />
        </SectionCard>
      </>}
  </section>;
}
