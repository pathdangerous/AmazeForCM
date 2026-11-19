import { Badge, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { catalogue, describeError, type DomainGroup } from "../api";
import { formatWhen } from "../theme";

// Phase 7.8, task 7.4: the products by domain. A card opens the product's page.
export default function CatalogueView({ devUser }: { devUser: string }) {
  const [groups, setGroups] = useState<DomainGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!devUser) return;
    catalogue(devUser)
      .then((list) => {
        setGroups(list);
        setError(null);
      })
      .catch((err: unknown) => {
        setGroups(null);
        setError(describeError(err));
      });
  }, [devUser]);

  return (
    <section>
      <Title order={2} mb="md">Catalogue</Title>
      {error && <Text className="bad" role="alert" data-testid="error">{error}</Text>}
      {!groups && !error && <Text className="muted">Loading the catalogue...</Text>}
      {groups && groups.length === 0 && <Text data-testid="no-products">No data products yet.</Text>}
      {groups?.map((group) => (
        <div key={group.domain}>
          <Title order={3} mt="md" mb="xs">{group.label}</Title>
          <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
            {group.products.map((p) => (
              <a key={p.code} href={`#/product/${p.code}`} data-testid={`catalogue-product-${p.code}`}
                 style={{ display: "block", padding: 12, border: "1px solid var(--amz-line)", borderRadius: 8,
                          color: "inherit", textDecoration: "none" }}>
                <strong>{p.name}</strong>{" "}
                {p.access === "entitled"
                  ? <Badge tt="none" color="var(--amz-success)">Available</Badge>
                  : <Badge tt="none" color="var(--amz-warning)" c="var(--amz-on-bright)">Access restricted</Badge>}
                <div>{p.description}</div>
                <div className="muted">
                  Owner: {p.owner_name}
                  {p.freshness ? ` | Updated ${formatWhen(p.freshness.released_at)}` : " | Not released yet"}
                </div>
              </a>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
