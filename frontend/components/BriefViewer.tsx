"use client";

// Brief viewer — UI-SPEC §5 (brief header + prose body + sources panel + action buttons).
//
// Rendering stack: react-markdown@^10 + remark-gfm@^4 wrapped in the .prose-brief
// container declared in app/globals.css (Plan 02-05). The react-markdown v10 API
// takes markdown source as `children` (not `source`) and plugins via `remarkPlugins`.
//
// Security (T-02-11-01):
//   - NO rehype-raw. Without it, raw HTML embedded in the markdown source is
//     auto-escaped by react-markdown and rendered as literal text. A prompt-injected
//     synthesizer that emits <script>alert(1)</script> would render it as visible
//     text, never execute it. This is the second line of defense after GUARD-01
//     sandbox delimiters at the synthesize layer (Plan 02-07).
//   - React additionally blocks href="javascript:*" (T-02-11-02).
//
// Rejected alternatives (CLAUDE.md / UI-SPEC):
//   - Vercel AI SDK / AI Elements: rejected (CLAUDE.md D-20 lock). Phase 2 briefs are
//     static finished documents; streaming-aware message rendering is inapplicable
//     until Phase 6 chat.
//   - react-pdf: D-21 locks print stylesheet as the v1 export path.
//   - Inline citation popovers (BRIEF-02): Phase 4 scope per D-07.
//   - Confidence badges (BRIEF-05): Phase 4.
//   - Share button: Phase 6 — "DO NOT RENDER" per UI-SPEC §5 Share button rule.
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { relativeTime } from "@/lib/utils";
import { BriefActions } from "@/components/BriefActions";
import { Scorecard } from "@/components/Scorecard";
import { SourcesPanel } from "@/components/SourcesPanel";
import { Button } from "@/components/ui/button";
import type { BriefResponse } from "@/lib/types";

export function BriefViewer({ brief }: { brief: BriefResponse }) {
  return (
    <>
      <section
        className="bg-card border-b border-border px-4 py-6 animate-fade-up motion-reduce:animate-none"
      >
        <div className="max-w-3xl mx-auto flex flex-col gap-4">
          <div className="flex items-center justify-between flex-wrap gap-2" data-no-print>
            <Button asChild variant="ghost" size="sm">
              <Link href="/investigations">← Back to library</Link>
            </Button>
            <BriefActions briefMarkdown={brief.brief_markdown} />
          </div>
          <div>
            <h1 className="font-display font-normal text-[2.25rem] leading-[1.08] tracking-tight">
              {brief.display_name}
            </h1>
            {/* Decorative accent rule — marks the brief title as the primary subject */}
            <div className="w-10 h-[2px] bg-primary mt-3 mb-3" aria-hidden />
            {/*
              .brief-meta is the hook the @media print block in app/globals.css uses
              to reduce subtitle size to 9pt on the PDF (UI-SPEC §8 Print stylesheet).
            */}
            <p className="brief-meta text-[13px] text-muted-foreground">
              One-page brief ·{" "}
              {brief.completed_at ? relativeTime(brief.completed_at) : "just now"}
            </p>
            {brief.scorecard && <Scorecard data={brief.scorecard} />}
          </div>
        </div>
      </section>

      <article className="max-w-3xl mx-auto px-4 py-8">
        <div className="prose prose-brief max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {brief.brief_markdown}
          </ReactMarkdown>
        </div>
        <SourcesPanel sources={brief.sources} />
      </article>
    </>
  );
}
