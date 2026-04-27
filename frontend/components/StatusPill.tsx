"use client";

// Status badge — UI-SPEC §Color §Status Badge Color Map.
//
// Uses inline style overrides (rationale from UI-SPEC): shadcn Badge's built-in
// variants don't map cleanly to the 5-state investigation lifecycle; status
// colors are semantic, not decorative.
//
// Rejected alternatives:
//   - Five custom Badge variants in variance-authority: introduces a
//     project-specific shadcn fork for one use case; inline style is clearer.
//   - Tailwind utility classes per status: requires a safelist for dynamic color
//     values (Tailwind JIT otherwise tree-shakes them).
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { InvestigationStatus } from "@/lib/types";

const STATUS_COPY: Record<InvestigationStatus, string> = {
  queued: "Queued…",
  gathering: "Searching & fetching sources…",
  synthesizing: "Writing the brief…",
  grounding: "Grounding citations…",
  complete: "Complete",
  failed: "Failed",
};

const STATUS_STYLE: Record<InvestigationStatus, { bg: string; fg: string }> = {
  queued: { bg: "#fef9c3", fg: "#854d0e" }, // yellow-100 / yellow-800
  gathering: { bg: "#fef3c7", fg: "#92400e" }, // amber-100 / amber-800
  synthesizing: { bg: "#ede9fe", fg: "#4c1d95" }, // violet-100 / violet-900
  grounding: { bg: "#ede9fe", fg: "#4c1d95" }, // violet (shares lane w/ synthesizing)
  complete: { bg: "#dcfce7", fg: "#14532d" }, // green-100 / green-900
  failed: { bg: "#fee2e2", fg: "#7f1d1d" }, // red-100 / red-900
};

export function StatusPill({
  status,
  label,
  className,
}: {
  status: InvestigationStatus;
  label?: string;
  className?: string;
}) {
  const s = STATUS_STYLE[status];
  return (
    <Badge
      className={cn("border-none", className)}
      style={{ backgroundColor: s.bg, color: s.fg }}
    >
      {label ?? STATUS_COPY[status]}
    </Badge>
  );
}
