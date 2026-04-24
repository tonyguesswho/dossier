"use client";

// InvestigationForm — UI-SPEC §3 (form), §Copywriting Contract, §7c (guardrail error).
//
// Plan 03-13 adds the "Pitch deck (PDF)" tab alongside the existing "Company
// name / URL" tab. Both paths share the context_hint textarea, submit button,
// and error copy. On PDF submit the file is POSTed multipart to
// /api/investigations/upload; on text submit it's JSON to /api/investigations.
//
// Client-side validation is defense-in-depth; the authoritative validator is
// FastAPI's CreateInvestigationBody / upload_deck_investigation (Plan 02-09 +
// 03-13) which enforces the same substring rejections + size caps.
//
// Rejected alternatives (UI-SPEC §3 + Plan 03-13):
//   - On-blur validation: distracting for a single-field form.
//   - Force users to type "https://": friction; auto-prefix is UX-correct.
//   - Show the raw API `detail` string on 422: may echo payload fragments
//     (UI-SPEC §7c). Use our own copy.
//   - Drag-and-drop PDF input: out of scope for demo-lite; <input type=file>
//     is enough.
//   - Separate deck-only component in a new route: adds routing + nav work
//     without UX payoff; tabbing one form is the minimum viable affordance.
import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { CreateInvestigationBody, InvestigationKind } from "@/lib/types";

const TLD_PATTERN = /\.(com|io|ai|co|net|org|app|dev)(\/|$)/i;

// Mirror of backend DECK_MAX_BYTES — fail fast in the browser before the
// Next.js proxy and FastAPI both 413 us.
const DECK_MAX_BYTES = 17 * 1024 * 1024;

// Mirror of schemas.py _REJECT_SUBSTRINGS; client-side match avoids a round-trip
// on obvious garbage, but the authoritative filter is on the server.
const REJECT_SUBSTRINGS = [
  "\x00",
  "`",
  "<script",
  "DROP TABLE",
  "ignore previous",
  "ignore all previous",
];

const formSchema = z.object({
  value: z
    .string()
    .min(1, "Please enter a company name or valid URL.")
    .max(200, "Company name must be under 200 characters."),
  context_hint: z
    .string()
    .max(500, "Context hint must be under 500 characters.")
    .optional(),
});

type Mode = "text" | "deck";

function detectKind(value: string): InvestigationKind {
  return value.includes("://") || TLD_PATTERN.test(value) ? "url" : "name";
}

function autoPrefix(value: string): string {
  if (!value.includes("://") && TLD_PATTERN.test(value)) {
    return "https://" + value;
  }
  return value;
}

function checkInjection(value: string): boolean {
  const v = value.toLowerCase();
  return REJECT_SUBSTRINGS.some((bad) => v.includes(bad.toLowerCase()));
}

// Map a machine-readable `detail` from the backend to a user-facing string.
// Centralized so both upload and text paths produce consistent copy.
function detailToMessage(status: number, detail: string | null): string {
  if (status === 429) {
    return "You've reached the 10-investigation daily limit. Try again tomorrow.";
  }
  if (status === 413 || detail === "pdf_too_large") {
    return "That PDF is larger than 17 MB. Please upload a smaller deck.";
  }
  if (status === 415 || detail === "pdf_required") {
    return "Please upload a PDF file.";
  }
  if (detail === "pdf_no_text_extracted") {
    return "We couldn't extract any text from that PDF. Scanned-image decks aren't supported yet.";
  }
  if (detail === "pdf_conversion_failed") {
    return "That PDF couldn't be parsed. Try re-exporting it from your source tool.";
  }
  if (detail === "empty_or_too_small") {
    return "That file looks empty. Please upload a real pitch deck.";
  }
  if (status === 422) {
    return "This input was rejected. Please enter a real company name or URL.";
  }
  return "Something went wrong. Please try again in a moment.";
}

export function InvestigationForm() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("text");
  const [value, setValue] = useState("");
  const [contextHint, setContextHint] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const switchMode = (next: Mode) => {
    setMode(next);
    setError(null);
  };

  const submitText = async (): Promise<void> => {
    const parsed = formSchema.safeParse({
      value: value.trim(),
      context_hint: contextHint.trim() || undefined,
    });
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Invalid input");
      return;
    }

    // Guardrail substring check (UI-SPEC §7c). Same rule set as FastAPI D-27.
    if (
      checkInjection(parsed.data.value) ||
      (parsed.data.context_hint && checkInjection(parsed.data.context_hint))
    ) {
      setError("This input was rejected. Please enter a real company name or URL.");
      return;
    }

    const kind = detectKind(parsed.data.value);
    const normalizedValue = kind === "url" ? autoPrefix(parsed.data.value) : parsed.data.value;

    const body: CreateInvestigationBody = {
      kind,
      value: normalizedValue,
      context_hint: parsed.data.context_hint,
    };

    const res = await fetch("/api/investigations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 202) {
      const out = (await res.json()) as { id: string };
      router.push(`/investigations/${out.id}`);
      return;
    }
    let detail: string | null = null;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON body */
    }
    setError(detailToMessage(res.status, detail));
  };

  const submitDeck = async (): Promise<void> => {
    if (!file) {
      setError("Please select a PDF file to upload.");
      return;
    }
    if (file.size > DECK_MAX_BYTES) {
      setError("That PDF is larger than 17 MB. Please upload a smaller deck.");
      return;
    }
    if (file.type && file.type !== "application/pdf") {
      setError("Please upload a PDF file.");
      return;
    }
    if (contextHint && checkInjection(contextHint)) {
      setError("This input was rejected. Please remove suspicious text from the hint.");
      return;
    }

    const fd = new FormData();
    fd.set("file", file);
    if (contextHint.trim()) fd.set("context_hint", contextHint.trim());

    const res = await fetch("/api/investigations/upload", {
      method: "POST",
      body: fd,
    });
    if (res.status === 202) {
      const out = (await res.json()) as { id: string };
      router.push(`/investigations/${out.id}`);
      return;
    }
    let detail: string | null = null;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON body */
    }
    setError(detailToMessage(res.status, detail));
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "text") {
        await submitText();
      } else {
        await submitDeck();
      }
    } catch {
      setError("Something went wrong. Please try again in a moment.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-6">
      <div
        role="tablist"
        aria-label="Investigation input type"
        className="flex gap-1 rounded-md border border-border p-1 bg-muted/40"
      >
        <button
          type="button"
          role="tab"
          aria-selected={mode === "text"}
          onClick={() => switchMode("text")}
          className={`flex-1 h-9 rounded-sm text-[13px] font-medium transition-colors ${
            mode === "text"
              ? "bg-background shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          Company name / URL
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "deck"}
          onClick={() => switchMode("deck")}
          className={`flex-1 h-9 rounded-sm text-[13px] font-medium transition-colors ${
            mode === "deck"
              ? "bg-background shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          Pitch deck (PDF)
        </button>
      </div>

      {mode === "text" ? (
        <div key="text-input" className="flex flex-col gap-1">
          <label htmlFor="value" className="text-[15px] font-semibold">
            Company name or URL
          </label>
          <Input
            key="input-text"
            id="value"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="Stripe  •  https://stripe.com  •  acme.com"
            maxLength={200}
            aria-describedby={error ? "form-error" : undefined}
            autoFocus
          />
        </div>
      ) : (
        <div key="deck-input" className="flex flex-col gap-1">
          <label htmlFor="deck" className="text-[15px] font-semibold">
            Pitch deck (PDF)
          </label>
          {/*
            Native <input type="file"> (not shadcn's <Input>) — the shadcn wrapper
            mangled the native "Choose File" button height so it wasn't clickable.
            Native rendering differs per OS/browser but always produces a working
            file chooser.
          */}
          <input
            key="input-file"
            id="deck"
            type="file"
            accept="application/pdf,.pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            aria-describedby={error ? "form-error" : "deck-help"}
            className="block w-full cursor-pointer rounded-md border border-input bg-transparent text-sm file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-muted file:px-3 file:py-2 file:text-[13px] file:font-medium file:text-foreground hover:file:bg-muted/80"
          />
          {file && (
            <p className="text-[13px] text-muted-foreground mt-1">
              Selected: <span className="font-medium text-foreground">{file.name}</span> ({Math.round(file.size / 1024)} KB)
            </p>
          )}
          <p id="deck-help" className="text-[13px] text-muted-foreground mt-1">
            PDF, under 17 MB. Text is extracted via MarkItDown — image-only scans
            aren&apos;t supported yet.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-1">
        <label htmlFor="hint" className="text-[15px] font-semibold">
          Context hint{" "}
          <span className="text-[13px] font-normal text-muted-foreground">(optional)</span>
        </label>
        <Textarea
          id="hint"
          value={contextHint}
          onChange={(e) => setContextHint(e.target.value)}
          placeholder='What are you meeting them about? e.g. "Series A, AI infra"'
          rows={2}
          maxLength={500}
        />
      </div>

      {error && (
        <p id="form-error" className="text-[13px] text-destructive -mt-2">
          {error}
        </p>
      )}

      <Button
        type="submit"
        className="w-full h-10"
        style={{ backgroundColor: "#4f46e5", color: "white" }}
        disabled={submitting}
      >
        {submitting ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin mr-2" />
            Investigating…
          </>
        ) : (
          "Investigate"
        )}
      </Button>
    </form>
  );
}
