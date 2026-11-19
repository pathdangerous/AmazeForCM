import { Alert, Badge, Button, Group, Table, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import {
  describeError, downloadProduct, ProductRefused, productGet, productPath,
  type ProductPage, type ProductPreview,
} from "../api";
import { formatWhen } from "../theme";

type Refusal = { kind: "not_entitled"; route: string } | { kind: "no_data" } | null;

// Phase 7.8, task 7.4: one product: header, data dictionary, a preview of the first 100 rows and two downloads.
export default function ProductView({ devUser, code }: { devUser: string; code: string }) {
  const [page, setPage] = useState<ProductPage | null>(null);
  const [preview, setPreview] = useState<ProductPreview | null>(null);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    const fail = (err: unknown) => {
      if (!current) return;
      if (err instanceof ProductRefused) setRefusal(err.refusal);
      else setError(describeError(err));
    };
    (async () => {
      const loaded = await productGet<ProductPage>(productPath(code), devUser);
      if (!current) return;
      setPage(loaded);
      if (loaded.access === "entitled") {
        const rows = await productGet<ProductPreview>(productPath(code, "/preview"), devUser);
        if (current) setPreview(rows);
      }
    })().catch(fail);
    return () => {
      current = false;
    };
  }, [devUser, code]);

  async function download(kind: "csv" | "xlsx") {
    setBusy(true);
    setError(null);
    try {
      await downloadProduct(code, kind, devUser);
    } catch (err) {
      if (err instanceof ProductRefused) setRefusal(err.refusal);
      else setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const route = refusal?.kind === "not_entitled" ? refusal.route : page?.access === "not_entitled_visible" ? page.route : null;
  const shown = preview?.rows.length ?? 0;
  const total = page?.freshness?.rows ?? 0;

  return (
    <section>
      <Text><a href="#/catalogue">Back to the catalogue</a></Text>
      {error && <Text className="bad" role="alert" data-testid="error">{error}</Text>}
      {!page && !error && !refusal && <Text className="muted">Loading the product...</Text>}
      {refusal?.kind === "no_data" && (
        <Text mt="md" data-testid="product-no-data">No data has been released for this product yet.</Text>
      )}
      {page && (
        <>
          <Title order={2} mt="sm">{page.name}</Title>
          <Group gap="xs" mb="xs">
            <Badge tt="none" variant="default">{page.domain_label}</Badge>
            <Badge tt="none" variant="default">{page.visibility}</Badge>
          </Group>
          <Text className="muted">
            Owner: {page.owner.name}
            {page.freshness ? ` | Updated ${formatWhen(page.freshness.released_at)}` : " | Not released yet"}
          </Text>
          <Text mt="sm">{page.description}</Text>

          <Title order={3} mt="lg" mb="xs">Data dictionary</Title>
          <Table.ScrollContainer minWidth={600}>
            <Table data-testid="product-dictionary" aria-label="Data dictionary" verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr><Table.Th>Column</Table.Th><Table.Th>Data item</Table.Th><Table.Th>Definition</Table.Th>
                  <Table.Th>Type</Table.Th><Table.Th>Unit</Table.Th></Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {page.dictionary.map((d) => (
                  <Table.Tr key={d.element_key}>
                    <Table.Td>{d.label}</Table.Td><Table.Td>{d.name}</Table.Td><Table.Td>{d.definition}</Table.Td>
                    <Table.Td>{d.data_type}</Table.Td><Table.Td>{d.unit ?? ""}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>

          {route !== null ? (
            <Alert mt="lg" color="var(--amz-info)" title="Access restricted" data-testid="product-route">{route}</Alert>
          ) : refusal?.kind !== "no_data" && (
            <>
              <Title order={3} mt="lg" mb="xs">Preview</Title>
              {preview && (
                <>
                  <Table.ScrollContainer minWidth={600} mah={420}>
                    <Table data-testid="product-preview" aria-label="Preview of the first rows" stickyHeader
                           verticalSpacing="xs">
                      <Table.Thead>
                        <Table.Tr>{preview.columns.map((c) => <Table.Th key={c}>{c}</Table.Th>)}</Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {preview.rows.map((row, i) => (
                          <Table.Tr key={i}>{row.map((v, j) => <Table.Td key={j}>{v === null ? "" : String(v)}</Table.Td>)}</Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  </Table.ScrollContainer>
                  {shown >= 100 && total > shown &&<Text className="muted" mt="xs">Showing {shown} of {total} rows</Text>}
                </>
              )}
              <Group gap="sm" mt="md">
                <Button variant="default" data-testid="product-download-csv" disabled={busy}
                        onClick={() => download("csv")}>Download CSV</Button>
                <Button variant="default" data-testid="product-download-xlsx" disabled={busy}
                        onClick={() => download("xlsx")}>Download Excel</Button>
              </Group>
            </>
          )}
        </>
      )}
    </section>
  );
}
