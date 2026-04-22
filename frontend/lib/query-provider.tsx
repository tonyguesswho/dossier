"use client";

// TanStack Query client provider — Phase 2 polling backbone per CONTEXT.md D-22.
//
// Why useState(() => new QueryClient()):
//   - Creates the client once per component instance, not on every render (would
//     invalidate caches each pass).
//   - Avoids module-scope instantiation that would share caches across users in
//     server-rendered builds.
//
// Rejected alternatives:
//   - SWR: TanStack Query is already installed and matches UI-SPEC §4 polling contract.
//   - Apollo / urql: overkill for REST endpoints; no GraphQL in Phase 2.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      })
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
