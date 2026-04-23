"use client";

// Brief action buttons — BRIEF-06 (Copy as Markdown) + BRIEF-07 (Export PDF).
//
// D-20: Copy copies the PRE-RENDERED markdown SOURCE, not the rendered HTML. This
// round-trips cleanly into Notion, Apple Notes, Superhuman. Copying HTML would paste
// visual-only content and lose list/table semantics.
//
// D-21: Export PDF calls window.print(); the print stylesheet lives in app/globals.css
// (Plan 02-05) and strips data-no-print chrome. react-pdf was the rejected alternative
// — acceptable for v1 per D-21; react-pdf is the Phase 4+ upgrade path.
//
// Rejected alternatives (UI-SPEC §5):
//   - Disabled "Share" button with tooltip: UI-SPEC §5 explicitly says "DO NOT RENDER
//     in Phase 2". BRIEF-08 is Phase 6.
//   - Native <button> without shadcn: shadcn Button gives focus rings + accessible
//     disabled states for free.
import { Clipboard, Printer } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

export function BriefActions({ briefMarkdown }: { briefMarkdown: string }) {
  async function onCopy() {
    try {
      if (
        typeof navigator !== "undefined" &&
        navigator.clipboard &&
        typeof window !== "undefined" &&
        window.isSecureContext
      ) {
        await navigator.clipboard.writeText(briefMarkdown);
      } else {
        // Fallback for localhost-http contexts where Clipboard API is gated.
        const ta = document.createElement("textarea");
        ta.value = briefMarkdown;
        ta.setAttribute("readonly", "");
        ta.style.position = "absolute";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      toast.success("Copied to clipboard");
    } catch {
      toast.error("Copy failed — try selecting the text manually.");
    }
  }

  return (
    <div className="flex gap-2" data-no-print>
      <Button variant="outline" onClick={onCopy} aria-label="Copy brief as Markdown">
        <Clipboard className="h-4 w-4 mr-2" />
        Copy as Markdown
      </Button>
      <Button
        variant="outline"
        onClick={() => window.print()}
        aria-label="Export brief as PDF"
      >
        <Printer className="h-4 w-4 mr-2" />
        Export PDF
      </Button>
      {/*
        Share button deferred to Phase 6 per CLAUDE.md + UI-SPEC §5 "DO NOT RENDER in
        Phase 2". Rejected: rendering a disabled "Share" with tooltip — adds implementation
        complexity for zero user value. Omit entirely.
      */}
    </div>
  );
}
