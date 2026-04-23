"use client";

// InvestigationForm — UI-SPEC §3 (form), §Copywriting Contract, §7c (guardrail error).
//
// Client-side validation is defense-in-depth; the authoritative validator is
// FastAPI's CreateInvestigationBody (Plan 02-09) which enforces the same
// substring rejections (D-27 / GUARD-03). Client-side failures short-circuit
// the network call.
//
// Rejected alternatives (UI-SPEC §3):
//   - On-blur validation: distracting for a single-field form.
//   - Force users to type "https://": friction; auto-prefix is UX-correct.
//   - Show the raw API `detail` string on 422: may echo payload fragments
//     (UI-SPEC §7c). Use our own copy.
//   - react-hook-form: one input + one optional textarea doesn't justify the
//     dependency surface; useState + Zod safeParse is enough.
import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { CreateInvestigationBody, InvestigationKind } from "@/lib/types";

const TLD_PATTERN = /\.(com|io|ai|co|net|org|app|dev)(\/|$)/i;

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

export function InvestigationForm() {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [contextHint, setContextHint] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

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

    setSubmitting(true);
    try {
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
      // Do NOT echo res detail into UI — UI-SPEC §7c bans echoing injection fragments.
      if (res.status === 422) {
        setError("This input was rejected. Please enter a real company name or URL.");
      } else if (res.status === 429) {
        setError("You've reached the 10-investigation daily limit. Try again tomorrow.");
      } else {
        setError("Something went wrong. Please try again in a moment.");
      }
    } catch {
      setError("Something went wrong. Please try again in a moment.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <label htmlFor="value" className="text-[15px] font-semibold">
          Company name or URL
        </label>
        <Input
          id="value"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Stripe  •  https://stripe.com  •  acme.com"
          maxLength={200}
          aria-describedby={error ? "form-error" : undefined}
          autoFocus
        />
      </div>

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
