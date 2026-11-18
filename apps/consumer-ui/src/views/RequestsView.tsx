import { Anchor, Button, Table, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { apiGet, apiPost, describeError, type FindSourceResponse, type RequestSummary } from "../api";

const OUTCOMES: Record<RequestSummary["outcome"], string> = {
  reuse: "Reuse",
  compose: "Compose",
  create_product: "Create product",
  raise_requirement: "Requirement raised",
};

// Every request of the signed-in requester and its outcome, newest first (decision 7).
export default function RequestsView({ devUser }: { devUser: string }) {
  const [requests, setRequests] = useState<RequestSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!devUser) return;
    apiGet<RequestSummary[]>("/api/consumer/requests", devUser)
      .then((list) => {
        setRequests(list);
        setError(null);
      })
      .catch((err: unknown) => {
        setRequests(null);
        setError(describeError(err));
      });
  }, [devUser, reload]);

  // For a requirement still "defined", such as one raised before phase 3 (plan, assumption 2).
  async function findSource(requirementId: string) {
    setBusy(requirementId);
    try {
      await apiPost<FindSourceResponse>(`/api/consumer/requirements/${requirementId}/find-source`, {}, devUser);
      window.location.hash = `#/requirement/${requirementId}`;
    } catch (err) {
      setError(describeError(err));
      setReload((n) => n + 1);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section>
      <Title order={2} mb="md">My requests</Title>
      {error && <Text className="bad" role="alert" data-testid="error">{error}</Text>}
      {requests && requests.length === 0 && <Text data-testid="no-requests">No requests yet.</Text>}
      {requests && requests.length > 0 && (
        <Table.ScrollContainer minWidth={800}>
          <Table data-testid="requests" verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr><Table.Th>When</Table.Th><Table.Th>Asked for</Table.Th><Table.Th>Outcome</Table.Th>
                <Table.Th>Products</Table.Th><Table.Th>Requirement</Table.Th><Table.Th>Message</Table.Th></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {requests.map((r) => (
                <Table.Tr key={r.id}>
                  <Table.Td>{new Date(r.created_at).toLocaleString()}</Table.Td>
                  <Table.Td>{r.query}</Table.Td>
                  <Table.Td data-testid="outcome">{OUTCOMES[r.outcome]}</Table.Td>
                  <Table.Td>{r.product_codes.join(", ") || "-"}</Table.Td>
                  <Table.Td data-testid="requirement-status">
                    {r.requirement_id ? (
                      <>
                        <Anchor href={`#/requirement/${r.requirement_id}`}>{(r.requirement_status ?? "").replaceAll("_", " ")}</Anchor>
                        {r.requirement_status === "defined" && (
                          <>
                            {" "}
                            <Button size="compact-xs" data-testid="find-source" disabled={busy !== null}
                                    onClick={() => findSource(r.requirement_id as string)}>
                              Find a source
                            </Button>
                          </>
                        )}
                      </>
                    ) : "-"}
                  </Table.Td>
                  <Table.Td>{r.message}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </section>
  );
}
