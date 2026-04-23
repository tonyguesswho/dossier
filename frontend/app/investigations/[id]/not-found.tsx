// Segment-level not-found fallback per Next.js 16 convention.
//
// Renders when notFound() is called from anywhere in the /investigations/[id]
// segment. The primary 404 path for Phase 2 is the inline fallback inside
// page.tsx (where the fetch 404 is observed on the client). This file exists
// so any future server-side fetch that calls notFound() within this segment
// lands on the same UI-SPEC §7e copy rather than the Next.js default 404.
//
// Rejected alternatives:
//   - Only rely on the inline client fallback: loses the server-side boundary
//     and leaks the default Next.js 404 page for server-thrown notFound().
//   - Redirect to /investigations on not-found: silently hides the cause; the
//     explicit not-found copy is kinder UX per UI-SPEC §7e.
import { FileX } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function NotFound() {
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
