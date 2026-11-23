import { Button, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { LoadingSkeleton, StatusChip } from "../../../ui-shared/phase711";
import { catalogue, describeError, type DomainGroup } from "../api";
import { formatWhen } from "../theme";

// Phase 7.8, task 7.4: the products by domain. A card opens the product's page.
export default function CatalogueView({ devUser }: { devUser: string }) {
  const [groups, setGroups] = useState<DomainGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!devUser) return;
    let current = true;
    catalogue(devUser)
      .then((list) => {
        if (!current) return;
        setGroups(list);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!current) return;
        setGroups(null);
        setError(describeError(err));
      });
    return () => { current = false; };
  }, [devUser, attempt]);

  return (
    <section>
      <Title order={2} mb="md">Catalogue</Title>
      {error && <><Text className="bad" role="alert" data-testid="error">{error}</Text>
        <Button variant="default" onClick={() => setAttempt((value) => value + 1)}>Try again</Button></>}
      {!groups && !error && <LoadingSkeleton label="Loading the catalogue..." />}
      {groups && groups.length === 0 && <div className="empty" data-testid="no-products">No data products yet.</div>}
      {groups?.map((group) => (
        <div key={group.domain}>
          <Title order={3} mt="md" mb="xs">{group.label}</Title>
          <div className="catalogue-products">
            {group.products.map((p) => (
              <a key={p.code} href={`#/product/${p.code}`} data-testid={`catalogue-product-${p.code}`}
                 style={{ display: "block", padding: 12, border: "1px solid var(--amz-line)", borderRadius: 8,
                          color: "inherit", textDecoration: "none" }}>
                <strong>{p.name}</strong>{" "}
                {p.access === "entitled"
                  ? <StatusChip tone="good">Available</StatusChip>
                  : <StatusChip tone="attention">Access restricted</StatusChip>}
                <div>{p.description}</div>
                <div className="muted">
                  Owner: {p.owner_name}
                  {p.freshness ? ` | Updated ${formatWhen(p.freshness.released_at)}` : " | Not released yet"}
                </div>
                {p.paused_since && (
                  <div data-testid={`catalogue-paused-${p.code}`}>{`Paused: not updating since ${formatWhen(p.paused_since)}`}</div>
                )}
              </a>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
