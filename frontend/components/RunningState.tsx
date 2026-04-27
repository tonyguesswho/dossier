"use client";

// Running-state view — UI-SPEC §4 + D-16 status enum + D-22 polling (parent owns
// the polling; this component is presentation-only).
//
// Rejected alternatives:
//   - Third-party stepper component: UI-SPEC §4 says "a simple flex row with gap-8,
//     no third-party stepper component."
//   - Server-sent events for progress: Phase 6 only (CLAUDE.md lock).
//   - Separate /investigations/{id}/failed route: UI-SPEC §4 keeps failed in the
//     same route as running — just a different branch of this component.
import { AlertCircle } from "lucide-react";
import Link from "next/link";
import { Fragment } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { InvestigationStatus } from "@/lib/types";

const STEP_ORDER: { key: string; label: string; matches: InvestigationStatus[] }[] = [
  { key: "queued", label: "Queued", matches: ["queued"] },
  { key: "searching", label: "Searching", matches: ["gathering"] },
  { key: "writing", label: "Writing", matches: ["synthesizing"] },
  { key: "grounding", label: "Grounding", matches: ["grounding"] },
];

const STATUS_TO_LABEL: Record<InvestigationStatus, string> = {
  queued: "Queued…",
  gathering: "Searching & fetching sources…",
  synthesizing: "Writing the brief…",
  grounding: "Grounding citations…",
  complete: "Complete",
  failed: "Investigation failed",
};

function stepIndex(status: InvestigationStatus): number {
  // Map status → index of current step (0-3), -1 if failed, last-index if complete.
  if (status === "failed") return -1;
  if (status === "complete") return STEP_ORDER.length - 1;
  return STEP_ORDER.findIndex((s) => s.matches.includes(status));
}

export function RunningState({
  displayName,
  status,
}: {
  displayName: string;
  status: InvestigationStatus;
}) {
  if (status === "failed") {
    return (
      <main
        className="max-w-2xl mx-auto px-4 py-20 text-center flex flex-col items-center gap-4"
        data-no-print
      >
        <AlertCircle className="h-8 w-8 text-destructive" aria-hidden />
        <h1 className="font-display font-normal text-[2rem] leading-[1.1]">Investigation failed</h1>
        <p className="text-[15px] text-muted-foreground max-w-md">
          {displayName} could not be researched — try a different name or URL.
        </p>
        <div className="flex gap-2 mt-2">
          <Button asChild variant="outline">
            <Link href={`/investigations/new?prefill=${encodeURIComponent(displayName)}`}>
              Try again
            </Link>
          </Button>
          <Button asChild variant="ghost">
            <Link href="/investigations">Back to library</Link>
          </Button>
        </div>
      </main>
    );
  }

  const idx = stepIndex(status);

  return (
    <main
      className="max-w-2xl mx-auto px-4 py-12 text-center flex flex-col items-center gap-6"
      data-no-print
    >
      <div
        role="status"
        aria-label={`Investigating ${displayName}`}
        className="h-10 w-10 rounded-full border-4 border-primary [border-top-color:transparent] animate-spin motion-reduce:animate-none"
      />
      <h1 className="font-display font-normal text-[2rem] leading-[1.1]">Investigating {displayName}…</h1>
      <p className="text-[15px] text-muted-foreground">{STATUS_TO_LABEL[status]}</p>

      <ol className="flex items-center mt-6">
        {STEP_ORDER.map((step, i) => {
          const isCurrent = i === idx;
          const isDone = i < idx;
          const isLast = i === STEP_ORDER.length - 1;
          return (
            <Fragment key={step.key}>
              <li
                aria-current={isCurrent ? "step" : undefined}
                className="flex flex-col items-center gap-1.5"
              >
                <span
                  className={cn(
                    "h-2.5 w-2.5 rounded-full transition-all duration-500",
                    isCurrent
                      ? "bg-primary step-dot-active"
                      : isDone
                        ? "bg-primary/50"
                        : "border border-stone-300 bg-background",
                  )}
                  aria-hidden
                />
                <span className={cn(
                  "text-[12px] whitespace-nowrap transition-colors",
                  isCurrent ? "text-foreground font-medium" : "text-muted-foreground",
                )}>
                  {step.label}
                </span>
              </li>
              {!isLast && (
                <div
                  className={cn(
                    "h-px w-10 sm:w-14 shrink-0 mb-[18px] mx-1 transition-colors duration-500",
                    isDone ? "bg-primary/40" : "bg-border",
                  )}
                  aria-hidden
                />
              )}
            </Fragment>
          );
        })}
      </ol>

      <p className="text-[13px] text-muted-foreground mt-4">
        This usually takes 2–4 minutes.
      </p>
    </main>
  );
}
