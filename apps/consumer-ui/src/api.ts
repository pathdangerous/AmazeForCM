// Small fetch helper for the Amaze API. The same file is in backend-ui and consumer-ui (plan, assumption 14).

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
