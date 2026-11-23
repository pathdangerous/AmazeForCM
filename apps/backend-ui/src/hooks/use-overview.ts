import { useEffect, useState } from "react";
import { apiGet, describeError, type OperationsOverview } from "../api";

export function useOverview(devUser: string, days: 1 | 7 | 30) {
  const [data, setData] = useState<OperationsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setData(null);
    setError(null);
    if (devUser) apiGet<OperationsOverview>(`/api/amaze/overview?days=${days}`, devUser)
      .then((next) => { if (active) setData(next); })
      .catch((err: unknown) => { if (active) setError(describeError(err)); });
    return () => { active = false; };
  }, [devUser, days, attempt]);
  return { data, error, retry: () => setAttempt((value) => value + 1) };
}
