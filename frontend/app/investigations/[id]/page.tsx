"use client";

// /investigations/{id} — status-driven view switcher between RunningState and
// BriefViewer, backed by TanStack Query polling. Final plan of Phase 2 Wave 3.
//
// Polling contract per CONTEXT.md D-22 + UI-SPEC §4:
//   useQuery({
//     queryKey: ['investigation', id, 'status'],
//     queryFn: fetchStatus,
//     refetchInterval: 2000,
//     // Disabled once the pipeline reaches a terminal state.
//   })
//
// When status transitions to complete/failed the refetchInterval returns false and
// a second dependent useQuery for the full brief payload runs (only on complete).
// This keeps the status poll cheap while running and pays the brief cost exactly once.
//
// Rejected alternatives:
//   - Server component with revalidate: adds a cache layer we don't need and would
//     make 2s polling awkward.
//   - Merged single query for both status + brief: would pay the brief cost every 2s
//     while the pipeline is running; brief is expensive-ish (markdown + sources). Two
//     queries wire the cheap/expensive split correctly.
//   - Inline citation popovers / confidence badges: Phase 4 (BRIEF-02 / BRIEF-05).
//   - Share button: Phase 6 "DO NOT RENDER" per UI-SPEC §5.
//
// Auth: the /api/investigations/* Next.js proxies from Plan 02-10 handle Clerk auth
// and forward JWT to FastAPI. A 404 from the proxy means either not-found OR
// belongs-to-another-user (D-24 row scoping) — both render the inline not-found UI.
import { useQuery } from "@tanstack/react-query";
import { FileX } from "lucide-react";
import Link from "next/link";
import { use } from "react";
import { Toaster } from "sonner";

import { BriefViewer } from "@/components/BriefViewer";
import { RunningState } from "@/components/RunningState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { BriefResponse, StatusResponse } from "@/lib/types";

const STATUS_POLL_MS = 2_000;

async function fetchStatus(id: string): Promise<StatusResponse> {
  const res = await fetch(`/api/investigations/${id}/status`, { cache: "no-store" });
  if (res.status === 404) {
    throw Object.assign(new Error("not_found"), { status: 404 });
  }
  if (!res.ok) throw new Error(`status failed: ${res.status}`);
  return res.json();
}

async function fetchBrief(id: string): Promise<BriefResponse> {
  const res = await fetch(`/api/investigations/${id}/brief`, { cache: "no-store" });
  if (res.status === 404) {
    throw Object.assign(new Error("not_found"), { status: 404 });
  }
  if (!res.ok) throw new Error(`brief failed: ${res.status}`);
  return res.json();
}

export default function InvestigationDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  // React 19 `use()` unwraps the Next.js 16 async params Promise at render time.
  // Next.js 16 mandates params be Promise-typed for dynamic route segments.
  const { id } = use(params);

  const statusQuery = useQuery<StatusResponse>({
    queryKey: ["investigation", id, "status"],
    queryFn: () => fetchStatus(id),
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "complete" || s === "failed" ? false : STATUS_POLL_MS;
    },
    // `retry: 1` is inherited from QueryProvider defaults; a 404 will still surface.
  });

  const briefQuery = useQuery<BriefResponse>({
    queryKey: ["investigation", id, "brief"],
    queryFn: () => fetchBrief(id),
    enabled: statusQuery.data?.status === "complete",
  });

  // 404 handling — inline per UI-SPEC §7e. Also covers cross-user access (FastAPI
  // returns 404 for rows belonging to another Clerk user, per Plan 02-09 T-02-09-05).
  const statusErr = statusQuery.error as { status?: number } | undefined;
  const briefErr = briefQuery.error as { status?: number } | undefined;
  const notFound = statusErr?.status === 404 || briefErr?.status === 404;

  if (notFound) {
    return (
      <main className="max-w-xl mx-auto px-4 py-20 text-center flex flex-col items-center gap-4">
        <FileX className="h-8 w-8 text-muted-foreground" aria-hidden />
        <h1 className="text-[30px] font-semibold">Investigation not found</h1>
        <p className="text-[15px] text-muted-foreground">
          This investigation doesn&apos;t exist or you don&apos;t have access to it.
        </p>
        <Button asChild style={{ backgroundColor: "#4f46e5", color: "white" }}>
          <Link href="/investigations">Back to library</Link>
        </Button>
      </main>
    );
  }

  // Initial load — render the loading skeleton (UI-SPEC §5 shape).
  if (!statusQuery.data) {
    return (
      <main className="max-w-3xl mx-auto px-4 py-8">
        <Skeleton className="h-8 w-64 mb-4" />
        <Skeleton className="h-4 w-48 mb-8" />
        <Skeleton className="h-6 w-32 mb-2" />
        <Skeleton className="h-20 w-full mb-6" />
        <Skeleton className="h-6 w-32 mb-2" />
        <Skeleton className="h-20 w-full" />
      </main>
    );
  }

  const { status } = statusQuery.data;

  // Brief view — only when status=complete AND the brief payload has landed.
  if (status === "complete" && briefQuery.data) {
    return (
      <div className="min-h-screen bg-background">
        <BriefViewer brief={briefQuery.data} />
        <Toaster position="bottom-right" />
      </div>
    );
  }

  // Running or failed state.
  return (
    <div className="min-h-screen bg-background">
      <header
        className="sticky top-0 z-40 h-14 flex items-center justify-between px-4 bg-card border-b border-border"
        data-no-print
      >
        <Link href="/investigations" className="text-[16px] font-semibold">
          Dossier
        </Link>
      </header>
      <RunningState
        displayName={statusQuery.data.display_name}
        status={status}
      />
      <Toaster position="bottom-right" />
    </div>
  );
}
