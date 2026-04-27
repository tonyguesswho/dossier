import Link from "next/link";

export default function Home() {
  return (
    <div className="min-h-screen bg-white flex flex-col">
      {/* Nav */}
      <header className="h-14 flex items-center justify-between px-6 border-b border-neutral-100">
        <span className="text-[16px] font-semibold tracking-tight">Dossier</span>
        <Link
          href="/investigations"
          className="text-sm font-medium text-neutral-600 hover:text-neutral-900 transition-colors"
        >
          Sign in →
        </Link>
      </header>

      {/* Hero */}
      <main className="flex-1 flex flex-col items-center justify-center px-6 text-center">
        <div className="max-w-2xl mx-auto space-y-6">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-50 text-indigo-700 text-xs font-medium">
            AI-powered · Every claim cited · 2–4 min
          </div>

          <h1 className="text-4xl sm:text-5xl font-semibold tracking-tight text-neutral-900 leading-tight">
            Stop Googling before<br className="hidden sm:block" /> every pitch meeting
          </h1>

          <p className="text-lg text-neutral-500 max-w-xl mx-auto leading-relaxed">
            Drop a company name, URL, or pitch deck. Get a one-page brief with six sections
            — founders, product, market, funding, risk flags, suggested questions —
            where every claim links back to the exact source it came from.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-3 pt-2">
            <Link
              href="/investigations"
              className="inline-flex items-center justify-center px-6 py-2.5 rounded-lg text-sm font-semibold text-white transition-opacity hover:opacity-90"
              style={{ backgroundColor: "#4f46e5" }}
            >
              Get started free
            </Link>
            <a
              href="https://github.com/tonyguesswho/dossier"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center px-6 py-2.5 rounded-lg text-sm font-semibold text-neutral-700 bg-neutral-100 hover:bg-neutral-200 transition-colors"
            >
              View on GitHub
            </a>
          </div>
        </div>
      </main>

      {/* Feature strip */}
      <section className="border-t border-neutral-100 bg-neutral-50">
        <div className="max-w-4xl mx-auto px-6 py-12 grid grid-cols-1 sm:grid-cols-3 gap-8">
          <div className="space-y-2">
            <div className="text-2xl font-bold text-neutral-900">100%</div>
            <div className="text-sm font-medium text-neutral-700">Citation precision</div>
            <p className="text-sm text-neutral-500">
              Every claim is verified against a verbatim source span before it reaches you.
              If it can't be proven, it's dropped.
            </p>
          </div>
          <div className="space-y-2">
            <div className="text-2xl font-bold text-neutral-900">27+</div>
            <div className="text-sm font-medium text-neutral-700">Sources per investigation</div>
            <p className="text-sm text-neutral-500">
              Exa web search, GitHub founder profiles, Firecrawl, NewsAPI, and Crunchbase
              gathered concurrently in one pass.
            </p>
          </div>
          <div className="space-y-2">
            <div className="text-2xl font-bold text-neutral-900">2–4 min</div>
            <div className="text-sm font-medium text-neutral-700">Start to cited brief</div>
            <p className="text-sm text-neutral-500">
              LangGraph reflection loop self-corrects thin sections before finalizing.
              Grounded chat included.
            </p>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="h-12 flex items-center justify-center px-6 border-t border-neutral-100">
        <p className="text-xs text-neutral-400">Dossier</p>
      </footer>
    </div>
  );
}
