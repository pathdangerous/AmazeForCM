import { useEffect, useState } from "react";
import { apiGet, describeError, type RequestSummary } from "../api";

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
  }, [devUser]);

  return (
    <section>
      <h2>My requests</h2>
      {error && <p className="bad" role="alert" data-testid="error">{error}</p>}
      {requests && requests.length === 0 && <p data-testid="no-requests">No requests yet.</p>}
      {requests && requests.length > 0 && (
        <table data-testid="requests">
          <thead>
            <tr><th>When</th><th>Asked for</th><th>Outcome</th><th>Products</th><th>Requirement</th><th>Message</th></tr>
          </thead>
          <tbody>
            {requests.map((r) => (
              <tr key={r.id}>
                <td>{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.query}</td>
                <td data-testid="outcome">{OUTCOMES[r.outcome]}</td>
                <td>{r.product_codes.join(", ") || "-"}</td>
                <td data-testid="requirement-status">{r.requirement_status ?? "-"}</td>
                <td>{r.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
