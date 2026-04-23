// Route-level loading skeleton — UI-SPEC §5 brief skeleton shape.
//
// Shown during the initial navigation to /investigations/[id] before the
// client component has mounted + the first status poll has resolved. The
// client component then shows an equivalent skeleton while statusQuery is
// in flight — the two overlap during the hand-off, which is intentional.
//
// Rejected: no loading boundary at all. Without this file, Next.js would show
// a blank canvas until the client component hydrates and runs its first query.
import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <main className="max-w-3xl mx-auto px-4 py-8">
      <Skeleton className="h-8 w-64 mb-4" />
      <Skeleton className="h-4 w-48 mb-8" />
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="mb-6">
          <Skeleton className="h-6 w-32 mb-2" />
          <Skeleton className="h-4 w-full mb-2" />
          <Skeleton className="h-4 w-5/6" />
        </div>
      ))}
    </main>
  );
}
