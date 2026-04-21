import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Dossier",
  description: "Cited-OSINT AI investigation agent for seed-stage VC first-meeting prep.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
