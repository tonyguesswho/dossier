// /investigations/new — the form page (UI-SPEC §3, INPUT-01/02/04, GUARD-03)
//
// Server component shell. All state + validation lives in InvestigationForm
// (client); this page just renders the chrome + header.
import Link from "next/link";

import { InvestigationForm } from "@/components/InvestigationForm";
import { Button } from "@/components/ui/button";

export const dynamic = "force-dynamic";

export default function NewInvestigationPage() {
  return (
    <div className="min-h-screen bg-background">
      <header
        className="sticky top-0 z-40 h-14 flex items-center justify-between px-4 bg-card border-b border-border"
        data-no-print
      >
        <Link href="/investigations" className="text-[16px] font-semibold">
          Dossier
        </Link>
        <Button asChild variant="ghost">
          <Link href="/investigations">Back to library</Link>
        </Button>
      </header>
      <main className="max-w-2xl mx-auto px-4 py-12">
        <h1 className="text-[20px] font-semibold mb-6">New Investigation</h1>
        <InvestigationForm />
      </main>
    </div>
  );
}
