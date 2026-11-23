import { Alert, Button, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import DataGrid from "../../../ui-shared/DataGrid";
import { CssDonut, LoadingSkeleton, SectionCard, StatusChip } from "../../../ui-shared/phase711";
import {
  describeError, downloadProduct, ProductRefused, productGet, productPath,
  type ProductPage, type ProductPreview,
} from "../api";
import { formatWhen } from "../theme";

type Refusal = { kind: "not_entitled"; route: string } | { kind: "no_data" } | null;

// Optional released-load metadata is rendered only when supplied and access is entitled.
export default function ProductView({ devUser, code }: { devUser: string; code: string }) {
  const [page, setPage] = useState<ProductPage | null>(null);
  const [preview, setPreview] = useState<ProductPreview | null>(null);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    setError(null);
    setRefusal(null);
    (async () => {
      const loaded = await productGet<ProductPage>(productPath(code), devUser);
      if (!current) return;
      setPage(loaded);
      if (loaded.access === "entitled") {
        const rows = await productGet<ProductPreview>(productPath(code, "/preview"), devUser);
        if (current) setPreview(rows);
      }
    })().catch((err: unknown) => {
      if (!current) return;
      if (err instanceof ProductRefused) setRefusal(err.refusal);
      else setError(describeError(err));
    });
    return () => { current = false; };
  }, [devUser, code, attempt]);

  async function download(kind: "csv" | "xlsx") {
    setBusy(true);
    setError(null);
    try { await downloadProduct(code, kind, devUser); }
    catch (err) {
      if (err instanceof ProductRefused) setRefusal(err.refusal);
      else setError(describeError(err));
    } finally { setBusy(false); }
  }
  const route = refusal?.kind === "not_entitled" ? refusal.route : page?.access === "not_entitled_visible" ? page.route : null;
  const restricted = refusal?.kind === "not_entitled" || page?.access === "not_entitled_visible";
  const showOperationalMetadata = page?.access === "entitled" && !restricted && refusal?.kind !== "no_data";
  const certification = showOperationalMetadata ? page?.certification : null;
  const lineage = showOperationalMetadata ? page?.lineage ?? [] : [];
  const shown = preview?.rows.length ?? 0;
  const total = page?.freshness?.rows ?? 0;

  return <section className="product-page">
    <div className="breadcrumbs"><a href="#/catalogue">Back to the catalogue</a>{page && <> / {page.domain_label} / {page.name}</>}</div>
    {error && <div className="amz-error"><Text className="bad" role="alert" data-testid="error">{error}</Text>
      <Button variant="default" mt="sm" onClick={() => setAttempt((value) => value + 1)}>Try again</Button></div>}
    {!page && !error && !refusal && <LoadingSkeleton label="Loading the product..." />}
    {refusal?.kind === "no_data" && <div className="empty" data-testid="product-no-data">No data has been released for this product yet.</div>}
    {page && <>
      <section className="amz-card product-hero">
        <div className="product-hero-main">
          <div className="product-tags">
            <span className="amz-chip" style={{ background: "#FEF1CC", color: "#5B4A12" }}>{page.domain_label}</span>
            <StatusChip>{page.visibility}</StatusChip>
            <StatusChip tone={restricted ? "attention" : "good"}>{restricted ? "Access restricted" : "You have access"}</StatusChip>
          </div>
          <Title order={2}>{page.name}</Title>
          <Text size="md">{page.description}</Text>
          <div className="product-metadata">
            <span>Owner: <strong>{page.owner.name}</strong></span>
            <span>{page.freshness ? `Updated ${formatWhen(page.freshness.released_at)}` : "Not released yet"}</span>
          </div>
          {page.paused_since && <Text data-testid="product-paused" size="sm" style={{ color: "#9A3412", background: "#FFEDD5", borderRadius: 6, padding: "6px 10px" }}>
            {`Paused: not updating since ${formatWhen(page.paused_since)}`}
          </Text>}
        </div>
        <div className="product-hero-actions">
          {certification && <div className="product-trust">
            <CssDonut value={certification.trust_score} label="Trust score" detail="" />
            <div><strong>Trust score</strong>
              <span>{certification.flagged_rows} of {certification.rows} rows flagged · certified</span>
            </div>
          </div>}
          {page.freshness && <div className="product-freshness"><span className="muted">Latest released data</span>
            <strong>{page.freshness.rows} rows · {page.columns.length} columns</strong></div>}
          {!restricted && refusal?.kind !== "no_data" && <>
            <Button data-testid="product-download-xlsx" disabled={busy} onClick={() => void download("xlsx")}>Download Excel</Button>
            <Button variant="default" data-testid="product-download-csv" disabled={busy} onClick={() => void download("csv")}>Download CSV</Button>
          </>}
        </div>
      </section>
      {restricted && <Alert color="var(--amz-info)" title="Access restricted" data-testid="product-route">{route ?? "Access has not been granted to this product."}</Alert>}
      {lineage.length > 0 && <SectionCard title="Where this data comes from" className="product-lineage-card">
        <ol className="product-lineage" aria-label="Data lineage">
          {lineage.map((entry, index) => <li key={`${entry.stage}-${index}`}>
            <div className="product-lineage-node">
              <span className="product-lineage-stage">{entry.stage}</span>
              <strong>{entry.name}</strong>
              {entry.rows !== null && <span className="product-lineage-rows">{entry.rows} rows</span>}
            </div>
            {index < lineage.length - 1 && <svg className="product-lineage-arrow" width="18" height="18" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg>}
          </li>)}
        </ol>
      </SectionCard>}
      <div className="product-tables">
        <SectionCard title="Data dictionary" bare actions={<span className="overview-note">{page.dictionary.length} elements</span>}>
          <DataGrid rows={page.dictionary} rowKey={(row) => row.element_key} pageSize={100}
            filters={false} sortable={false} showPagination={page.dictionary.length > 100}
            aria-label="Data dictionary" data-testid="product-dictionary" columns={[
              { key: "column", label: "Column", value: (row) => row.label },
              { key: "name", label: "Data item", value: (row) => row.name },
              { key: "definition", label: "Definition", value: (row) => row.definition },
              { key: "type", label: "Type", value: (row) => row.data_type },
              { key: "unit", label: "Unit", value: (row) => row.unit, render: (row) => row.unit ?? "" },
            ]} emptyMessage="No dictionary entries are available." />
        </SectionCard>
        {!restricted && refusal?.kind !== "no_data" && <SectionCard title="Preview" bare
          actions={<span className="overview-note">First {shown}{page.freshness ? ` of ${total}` : ""} rows</span>}>
          {!preview && !error ? <LoadingSkeleton label="Loading preview" /> : preview ? <DataGrid<{ index: number; row: unknown[] }>
            rows={preview.rows.map((row, index) => ({ index, row }))} rowKey={(row) => row.index} pageSize={100}
            filters={false} sortable={false} showPagination={false}
            aria-label="Preview of the first rows" data-testid="product-preview"
            columns={preview.columns.map((column, index) => ({
              key: `${column}-${index}`, label: column,
              value: (entry) => entry.row[index] == null ? "" : String(entry.row[index]),
            }))} emptyMessage="No preview rows are available." />
            : <div className="empty">The preview could not be loaded. Use Try again above.</div>}
          {shown >= 100 && total > shown && <Text className="muted" p="sm">Showing {shown} of {total} rows</Text>}
        </SectionCard>}
      </div>
    </>}
  </section>;
}
