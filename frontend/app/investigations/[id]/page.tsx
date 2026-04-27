"use client";

// /investigations/{id} — status-driven view switcher between RunningState and
// BriefViewer, backed by TanStack Query polling.
//
// Polling contract (2s interval, stops on terminal state) and the dependent
// brief query live in useInvestigation — this page only owns rendering.
//
// Rejected alternatives:
//   - Server component with revalidate: adds a cache layer we don't need and
//     would make 2s polling awkward.
//   - Merged single query for both status + brief: would pay the brief cost
//     every 2s while the pipeline is running. Two queries wire the
//     cheap/expensive split correctly.
//   - Inline citation popovers / confidence badges: Phase 4 (BRIEF-02 / BRIEF-05).
//   - Share button: Phase 6 "DO NOT RENDER" per UI-SPEC §5.
//
// Auth: the /api/investigations/* Next.js proxies handle Clerk auth and forward
// JWT to FastAPI. A 404 means not-found OR belongs-to-another-user (D-24 row
// scoping) — both render the inline not-found UI.
import { FileX } from "lucide-react";
import Link from "next/link";
import { use } from "react";
import { Toaster } from "sonner";

import { BriefViewer } from "@/components/BriefViewer";
import { ChatPane } from "@/components/ChatPane";
import { RunningState } from "@/components/RunningState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useInvestigation } from "@/lib/hooks/use-investigation";

export default function InvestigationDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  // React 19 `use()` unwraps the Next.js 16 async params Promise at render time.
  const { id } = use(params);
  const { statusQuery, briefQuery, notFound } = useInvestigation(id);

  if (notFound) {
    return (
      <main className="max-w-xl mx-auto px-4 py-20 text-center flex flex-col items-center gap-4">
        <FileX className="h-8 w-8 text-muted-foreground" aria-hidden />
        <h1 className="font-display font-normal text-[2rem] leading-[1.1]">Investigation not found</h1>
        <p className="text-[15px] text-muted-foreground">
          This investigation doesn&apos;t exist or you don&apos;t have access to it.
        </p>
        <Button asChild>
          <Link href="/investigations">Back to library</Link>
        </Button>
      </main>
    );
  }

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

  if (status === "complete" && briefQuery.data) {
    return (
      <div className="min-h-screen bg-background">
        <BriefViewer brief={briefQuery.data} />
        <ChatPane investigationId={id} />
        <Toaster position="bottom-right" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <header
        className="sticky top-0 z-40 h-14 flex items-center justify-between px-4 bg-card border-b border-border"
        data-no-print
      >
        <nav aria-label="Main navigation">
          <Link href="/investigations" className="text-[16px] font-semibold">
            Dossier
          </Link>
        </nav>
      </header>
      <RunningState
        displayName={statusQuery.data.display_name}
        status={status}
      />
      <Toaster position="bottom-right" />
    </div>
  );
}
