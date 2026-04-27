"use client";

import { useQuery } from "@tanstack/react-query";

import type { BriefResponse, StatusResponse } from "@/lib/types";

const STATUS_POLL_MS = 2_000;

// Centralised query key factory — every component that touches investigation
// queries imports from here so key changes propagate automatically.
export const investigationKeys = {
  status: (id: string) => ["investigation", id, "status"] as const,
  brief: (id: string) => ["investigation", id, "brief"] as const,
  chat: (id: string) => ["investigation", id, "chat"] as const,
} as const;

async function fetchStatus(id: string): Promise<StatusResponse> {
  const res = await fetch(`/api/investigations/${id}/status`, { cache: "no-store" });
  if (res.status === 404) throw Object.assign(new Error("not_found"), { status: 404 });
  if (!res.ok) throw new Error(`status ${res.status}`);
  return res.json();
}

async function fetchBrief(id: string): Promise<BriefResponse> {
  const res = await fetch(`/api/investigations/${id}/brief`, { cache: "no-store" });
  if (res.status === 404) throw Object.assign(new Error("not_found"), { status: 404 });
  if (!res.ok) throw new Error(`brief ${res.status}`);
  return res.json();
}

export function useInvestigation(id: string) {
  const statusQuery = useQuery<StatusResponse>({
    queryKey: investigationKeys.status(id),
    queryFn: () => fetchStatus(id),
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "complete" || s === "failed" ? false : STATUS_POLL_MS;
    },
  });

  const briefQuery = useQuery<BriefResponse>({
    queryKey: investigationKeys.brief(id),
    queryFn: () => fetchBrief(id),
    enabled: statusQuery.data?.status === "complete",
  });

  const statusErr = statusQuery.error as { status?: number } | undefined;
  const briefErr = briefQuery.error as { status?: number } | undefined;
  const notFound = statusErr?.status === 404 || briefErr?.status === 404;

  return { statusQuery, briefQuery, notFound };
}
