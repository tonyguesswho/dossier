import Link from "next/link";

const SAMPLE_CITATIONS = [
  {
    num: "1",
    url: "techcrunch.com/2024/01/acme-seed",
    quote: '"Chen and Webb left Stripe\'s infra team to start Acme in January 2022"',
  },
  {
    num: "2",
    url: "linkedin.com/in/sarah-chen",
    quote: '"M.S. Computer Science (Distributed Systems), Carnegie Mellon, 2016"',
  },
  {
    num: "3",
    url: "crunchbase.com/acme-corp/funding",
    quote: '"Acme Corp raised a $4.2M seed round led by Benchmark in March 2024"',
  },
];

export default function Home() {
  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Nav */}
      <header className="h-14 flex items-center justify-between px-6 border-b border-border">
        <span className="text-[16px] font-semibold tracking-tight">Dossier</span>
        <Link
          href="/investigations"
          className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
        >
          Sign in →
        </Link>
      </header>

      {/* Hero */}
      <main className="flex-1">
        <div className="max-w-6xl mx-auto px-6 py-14 lg:py-20">
          <div className="grid lg:grid-cols-[1fr_1.1fr] gap-12 lg:gap-20 items-start">

            {/* Left: Statement + fact rows + CTA */}
            <div className="flex flex-col gap-10">
              <div className="flex flex-col gap-5">
                <h1
                  className="font-display font-normal text-[3rem] sm:text-[3.75rem] leading-[1.04] tracking-[-0.01em] text-foreground animate-fade-up motion-reduce:animate-none"
                  style={{ animationDelay: "40ms" }}
                >
                  Know the company<br />before the call.
                </h1>
                <p
                  className="text-[15px] text-muted-foreground leading-relaxed max-w-[26rem] animate-fade-up motion-reduce:animate-none"
                  style={{ animationDelay: "140ms" }}
                >
                  Paste a name, URL, or pitch deck. A few minutes later:
                  a one-page brief where every claim links to the exact sentence it came from.
                  No hallucinations. No summaries without sources.
                </p>
              </div>

              {/* Term-sheet style fact rows */}
              <div className="border-t border-border animate-fade-up motion-reduce:animate-none" style={{ animationDelay: "240ms" }}>
                <dl>
                  <div className="flex gap-5 py-3 border-b border-border">
                    <dt className="font-mono text-[11px] text-muted-foreground uppercase tracking-wider w-[5.5rem] shrink-0 pt-[2px]">
                      Sources
                    </dt>
                    <dd className="text-[14px] text-foreground leading-snug">
                      27+ per run — web, GitHub, news, Crunchbase, pitch deck — gathered concurrently
                    </dd>
                  </div>
                  <div className="flex gap-5 py-3 border-b border-border">
                    <dt className="font-mono text-[11px] text-muted-foreground uppercase tracking-wider w-[5.5rem] shrink-0 pt-[2px]">
                      Cited
                    </dt>
                    <dd className="text-[14px] text-foreground leading-snug">
                      Every claim verified against a verbatim span. Unverifiable claims are dropped, not guessed
                    </dd>
                  </div>
                  <div className="flex gap-5 py-3 border-b border-border">
                    <dt className="font-mono text-[11px] text-muted-foreground uppercase tracking-wider w-[5.5rem] shrink-0 pt-[2px]">
                      Sections
                    </dt>
                    <dd className="text-[14px] text-foreground leading-snug">
                      Founders · Product · Market · Funding · Risk flags · Suggested questions
                    </dd>
                  </div>
                  <div className="flex gap-5 py-3">
                    <dt className="font-mono text-[11px] text-muted-foreground uppercase tracking-wider w-[5.5rem] shrink-0 pt-[2px]">
                      Follow-up
                    </dt>
                    <dd className="text-[14px] text-foreground leading-snug">
                      Grounded chat after the brief — every answer cites the same source chunks
                    </dd>
                  </div>
                </dl>
              </div>

              <div className="animate-fade-up motion-reduce:animate-none" style={{ animationDelay: "320ms" }}>
                <Link
                  href="/investigations"
                  className="inline-flex items-center justify-center px-5 py-2.5 rounded bg-primary text-primary-foreground text-[14px] font-semibold hover:opacity-90 transition-opacity"
                >
                  Start an investigation
                </Link>
              </div>
            </div>

            {/* Right: Sample brief output */}
            <div className="lg:pt-1 animate-fade-up motion-reduce:animate-none" style={{ animationDelay: "120ms" }}>
              <div className="border border-border bg-card overflow-hidden rounded-sm">

                {/* Brief header */}
                <div className="px-5 py-3.5 border-b border-border bg-background/60">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[13px] font-semibold text-foreground truncate">
                      Acme Corp — Pre-Meeting Brief
                    </span>
                    <span className="font-mono text-[11px] text-muted-foreground whitespace-nowrap">
                      2 min ago
                    </span>
                  </div>
                  <div className="flex items-center gap-2.5 mt-2">
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 border border-emerald-200 px-2.5 py-0.5 text-[11px] font-medium text-emerald-800">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 inline-block" />
                      Citation precision: 100%
                    </span>
                    <span className="font-mono text-[11px] text-muted-foreground">
                      31 sources
                    </span>
                  </div>
                </div>

                {/* Brief body */}
                <div className="px-5 py-5 text-[13px] leading-[1.65] space-y-5">

                  {/* Founders section */}
                  <div>
                    <p className="font-mono text-[10px] text-muted-foreground uppercase tracking-[0.12em] mb-2">
                      Founders
                    </p>
                    <p className="text-foreground">
                      Sarah Chen and Marcus Webb co-founded Acme Corp in January 2022 after leaving
                      Stripe&apos;s infrastructure team.
                      <sup className="text-primary font-semibold ml-[2px] text-[10px] leading-none">[1]</sup>
                      {" "}Chen holds an MS in distributed systems from CMU
                      <sup className="text-primary font-semibold ml-[2px] text-[10px] leading-none">[2]</sup>
                      {" "}and previously led reliability engineering at Cloudflare.
                      Webb was a founding engineer at Plaid (2018–2021) before the Visa acquisition.
                    </p>
                  </div>

                  {/* Funding section */}
                  <div>
                    <p className="font-mono text-[10px] text-muted-foreground uppercase tracking-[0.12em] mb-2">
                      Funding
                    </p>
                    <p className="text-foreground">
                      $4.2M seed led by Benchmark in March 2024,
                      <sup className="text-primary font-semibold ml-[2px] text-[10px] leading-none">[3]</sup>
                      {" "}with participation from First Round Capital and three operator angels.
                      Previous $800K pre-seed in Q3 2022 from YC W23.
                    </p>
                  </div>

                  {/* Risk flags */}
                  <div>
                    <p className="font-mono text-[10px] text-muted-foreground uppercase tracking-[0.12em] mb-2">
                      Risk flags
                    </p>
                    <ul className="text-foreground space-y-1 list-none pl-0">
                      <li className="flex gap-2">
                        <span className="text-warning mt-px shrink-0">▲</span>
                        <span>No public enterprise customers named; revenue timeline unclear</span>
                      </li>
                      <li className="flex gap-2">
                        <span className="text-warning mt-px shrink-0">▲</span>
                        <span>Chen&apos;s LinkedIn updated 3 weeks ago — potential pivot signal</span>
                      </li>
                    </ul>
                  </div>

                  {/* Citations */}
                  <div className="border-t border-border pt-4 space-y-2">
                    <p className="font-mono text-[10px] text-muted-foreground uppercase tracking-[0.12em] mb-3">
                      Sources
                    </p>
                    {SAMPLE_CITATIONS.map(({ num, url, quote }) => (
                      <div key={num} className="flex gap-2.5 items-start">
                        <span className="text-primary font-semibold text-[10px] font-mono mt-[1px] shrink-0">
                          [{num}]
                        </span>
                        <div className="min-w-0">
                          <span className="font-mono text-[11px] text-muted-foreground truncate block">
                            {url}
                          </span>
                          <span className="text-[11px] text-foreground/60 italic">
                            {quote}
                          </span>
                        </div>
                      </div>
                    ))}
                    <p className="font-mono text-[11px] text-muted-foreground pt-1">
                      + 28 more sources ↓
                    </p>
                  </div>
                </div>
              </div>

              {/* Caption under card */}
              <p className="text-[12px] text-muted-foreground mt-3 pl-1">
                Sample output. Every superscript links to a verbatim quote from the source.
              </p>
            </div>

          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="h-12 flex items-center justify-center px-6 border-t border-border">
        <p className="text-xs text-muted-foreground">
          Dossier · Cited company research in minutes
        </p>
      </footer>
    </div>
  );
}
