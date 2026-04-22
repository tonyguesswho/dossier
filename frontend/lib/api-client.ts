// Server-only FastAPI fetch helper. Call from Next.js route handlers only.
//
// Per CONTEXT.md D-23: route handlers are thin Clerk-authed proxies to FastAPI.
//   - Route handler calls `apiFetch('/investigations', {...})`.
//   - apiFetch pulls the Clerk JWT server-side and forwards it as Bearer.
//   - FastAPI verifies the JWT via require_clerk_user_id (Plan 02-03).
//   - Business logic stays in FastAPI (ARCHITECTURE.md §9 anti-pattern).
//
// Rejected alternatives:
//   - Client-side fetch with Clerk session in browser: exposes backend URL + JWT lifecycle
//     to the browser; Phase 2 favors proxy pattern for CORS simplicity + rate limiting.
//   - Cookie forward without JWT: Clerk session cookies aren't valid cross-origin.
//   - Inline fetch per route: duplicates auth-token handling; centralize here.
//   - Fetch caching ("force-cache"): investigation data is user-scoped + live; cache is wrong.
import { auth } from "@clerk/nextjs/server";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export class ApiFetchError extends Error {
  constructor(
    public status: number,
    public detail: string,
    public path: string,
  ) {
    super(`apiFetch ${path} failed (${status}): ${detail}`);
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  // Next.js 16 App Router: auth() returns a Promise of the auth object.
  const { getToken } = await auth();
  const token = await getToken();

  const headers = new Headers(init.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (!headers.has("Content-Type") && init.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }

  const url = path.startsWith("http") ? path : `${BACKEND_URL}${path}`;

  const response = await fetch(url, { ...init, headers, cache: "no-store" });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = String(body.detail);
      }
    } catch {
      /* not JSON */
    }
    throw new ApiFetchError(response.status, detail, path);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
