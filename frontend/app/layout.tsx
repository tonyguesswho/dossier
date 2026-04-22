// Root layout — wires Clerk auth, TanStack Query, Geist fonts, Tailwind tokens.
//
// Per UI-SPEC.md:
//   §Font Loading: Geist Sans + Mono via next/font/google; both .variable classes on <html>.
//   §Tailwind 4 Theme Override: --font-sans / --font-mono bind Tailwind utilities.
//   §1 Auth Gate: ClerkProvider appearance uses project accent colors.
//
// Per CONTEXT.md:
//   D-22: TanStack Query is the polling backbone; client in lib/query-provider.tsx.
//   D-24: Clerk session validated by middleware.ts; JWT forwarded to FastAPI via api-client.ts.
//
// Rejected alternatives:
//   - Load Geist via <link>: loses next/font automatic subsetting + CLS prevention.
//   - QueryProvider per page: duplicates client instantiation.
import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { Geist, Geist_Mono } from "next/font/google";

import { QueryProvider } from "@/lib/query-provider";
import "./globals.css";

const geistSans = Geist({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Dossier",
  description: "Cited-OSINT AI investigation agent for seed-stage VC first-meeting prep.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider
      appearance={{
        variables: {
          colorPrimary: "#4f46e5",
          colorBackground: "#fafaf9",
          fontFamily: "Geist Sans, sans-serif",
        },
      }}
    >
      <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`}>
        <body>
          <QueryProvider>{children}</QueryProvider>
        </body>
      </html>
    </ClerkProvider>
  );
}
