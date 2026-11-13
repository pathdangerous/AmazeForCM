// Small fetch helper for the Amaze API, plus the shapes backend-ui reads.
// consumer-ui has its own copy of the helper (plan, phase 0 assumption 14).

export type Health = {
  status: "ok" | "error";
  database: "ok" | "unreachable" | "not_configured";
  adapters: Record<string, string>;
  unavailable: string[]; // selected adapters that are still Azure stubs
};

export type User = {
  id: string;
  display_name: string;
  email: string;
  roles: string[];
  app: "backend" | "consumer";
};

export type DomainCount = { domain: string; label: string; count: number };

export type GlossaryElement = {
  id: string;
  created_at: string;
  key: string;
  version: number;
  name: string;
  domain: string;
  data_type: string;
  definition: string;
  aliases: string[];
  created_by: string;
  proposal_id: string | null;
};

export type ElementDetail = { element: GlossaryElement; history: GlossaryElement[] };

export type Feed = {
  id: string;
  created_at: string;
  code: string;
  name: string;
  stage: "candidate" | "registered" | "rejected";
  requirement_id: string | null;
  owner_id: string | null;
  source_ref: string | null;
  registered_at: string | null;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// GET a JSON endpoint. acceptStatuses lists non-2xx statuses whose body is still wanted (health's 503).
export async function apiGet<T>(path: string, devUser?: string, acceptStatuses: number[] = []): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (devUser) headers["X-Dev-User"] = devUser;
  const response = await fetch(path, { headers });
  if (!response.ok && !acceptStatuses.includes(response.status)) {
    throw new ApiError(response.status, `${path} returned HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

// A sentence for the person, never a bare status code.
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Not signed in: choose a dev user above.";
    if (err.status === 403) return "Not allowed: this view needs a backend role.";
    if (err.status === 503) return "Service unavailable: check the Status view.";
    return err.message;
  }
  return "The API cannot be reached. Is it running on port 8000?";
}
