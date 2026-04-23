// Route-level loading skeleton for /investigations (LIB-01) + /investigations/new.
// UI-SPEC §2 "Loading state: three Skeleton rows".
import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <Skeleton className="h-7 w-48 mb-4" />
      <div className="flex flex-col gap-2">
        <Skeleton className="h-[52px] w-full" />
        <Skeleton className="h-[52px] w-full" />
        <Skeleton className="h-[52px] w-full" />
      </div>
    </div>
  );
}
