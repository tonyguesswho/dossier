// Collapsible sources list — UI-SPEC §5 Sources panel.
//
// The @media print stylesheet in app/globals.css (Plan 02-05) expands <details>
// automatically for printed output, so the PDF includes all source links even
// though the HTML view starts collapsed.
//
// Rejected alternatives:
//   - shadcn Accordion: UI-SPEC §5 explicitly calls for native <details>/<summary>,
//     which is cheaper and prints reliably without JavaScript.
//   - Always-expanded list: buries the brief body; users want the brief first,
//     sources on demand.
//   - New-tab vs same-tab: UI-SPEC locks target="_blank" — keeps the brief open
//     while the VC cross-references.
import { Badge } from "@/components/ui/badge";
import type { SourceListItem } from "@/lib/types";

export function SourcesPanel({ sources }: { sources: SourceListItem[] }) {
  if (sources.length === 0) return null;

  return (
    <section className="mt-12">
      <details className="prose-brief">
        <summary className="text-[16px] font-semibold cursor-pointer">
          Sources ({sources.length})
        </summary>
        <ul className="list-none pl-0 flex flex-col gap-2 mt-4">
          {sources.map((s) => (
            <li key={s.id} className="flex items-center gap-2">
              <Badge variant="secondary" className="text-[11px] uppercase">
                {s.source_kind}
              </Badge>
              <a
                href={s.url}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-[13px] text-muted-foreground truncate max-w-[80ch]"
              >
                {s.url}
              </a>
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
