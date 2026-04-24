import type { Scorecard as ScorecardType } from "@/lib/types";

// Scorecard — Phase 4-lite eval visibility baked into every brief.
//
// Why this component exists:
//   The project's headline claim is "every cited span verifies against its
//   source." A scorecard on each brief turns that claim from marketing into
//   a product feature reviewers can point at without running the CLI eval.
//
// Rendering rules (kept deliberately small so it reads as data, not chrome):
//   - 100% precision → green pill ("every citation verifies")
//   - ≥90% precision → emerald pill
//   - <90% precision → amber pill (rare; would indicate grounder drift)
//   - Grounding rate shown as a secondary number — context, not primary KPI,
//     since a low rate is by-design fail-safety, not failure.
export function Scorecard({ data }: { data: ScorecardType }) {
  const { citation_precision, grounding_rate, total_claims, grounded_claims } = data;
  const precisionPct = Math.round(citation_precision * 100);
  const groundingPct = Math.round(grounding_rate * 100);

  const precisionStyle =
    precisionPct >= 100
      ? "bg-emerald-50 text-emerald-900 border-emerald-200"
      : precisionPct >= 90
      ? "bg-emerald-50 text-emerald-900 border-emerald-200"
      : "bg-amber-50 text-amber-900 border-amber-200";

  return (
    <div
      className="flex flex-wrap items-center gap-2 text-[12px] mt-3"
      aria-label="Citation scorecard"
    >
      <span
        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-medium ${precisionStyle}`}
        title={`${grounded_claims} grounded claim${grounded_claims === 1 ? "" : "s"}, ${precisionPct}% of quoted spans verify as substrings of their cited chunk.`}
      >
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-current"></span>
        Citation precision: {precisionPct}%
      </span>
      <span
        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-muted-foreground"
        title="Share of synthesized claims the grounder accepted. The rest were dropped rather than cited with a hallucinated URL — honest fail-safety, not failure."
      >
        Grounding: {grounded_claims}/{total_claims} ({groundingPct}%)
      </span>
    </div>
  );
}
