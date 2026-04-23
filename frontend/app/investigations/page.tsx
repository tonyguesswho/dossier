// /investigations — library page (UI-SPEC §2, LIB-01, LIB-02, LIB-03)
//
// Server component shell; the list + mutations live in LibraryList (client).
// Top bar per UI-SPEC §2: sticky, 56px h-14, wordmark left, "New investigation" right.
//
// Rejected alternatives:
//   - Prefetch list SSR-side via listInvestigations() + hydrate TanStack cache:
//     worth it only if there's a meaningful TTFB win; in practice /investigations
//     is behind Clerk so middleware already cost us a round trip. Phase 7 may
//     revisit with queryClient.prefetchQuery.
//   - Server-render the rows directly: loses client-side polling + optimistic delete.
import Link from "next/link";
import { Toaster } from "sonner";

import { LibraryList } from "@/components/LibraryList";
import { Button } from "@/components/ui/button";

export const dynamic = "force-dynamic";

export default function InvestigationsPage() {
  return (
    <div className="min-h-screen bg-background">
      <header
        className="sticky top-0 z-40 h-14 flex items-center justify-between px-4 bg-card border-b border-border"
        data-no-print
      >
        <Link href="/investigations" className="text-[16px] font-semibold">
          Dossier
        </Link>
        <Button asChild style={{ backgroundColor: "#4f46e5", color: "white" }}>
          <Link href="/investigations/new">+ New Investigation</Link>
        </Button>
      </header>
      <main className="max-w-3xl mx-auto px-4 py-8">
        <h1 className="text-[20px] font-semibold mb-4">Investigations</h1>
        <LibraryList />
      </main>
      <Toaster position="bottom-right" />
    </div>
  );
}
