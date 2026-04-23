// GET /api/investigations → FastAPI GET /investigations (LIB-01)
// POST /api/investigations → FastAPI POST /investigations (INPUT-01/02/04 + GUARD-03)
//
// Thin Clerk-authed proxy per CONTEXT.md D-23. NO business logic here.
// Rate limit + input validation + user scoping all happen in FastAPI (Plan 02-09).
//
// Rejected alternatives:
//   - Return apiFetch result directly: loses the chance to map ApiFetchError.status
//     → same client-facing status (critical for 422/429 → UI-SPEC error copy).
//   - Custom request validation here: duplicates Pydantic validators in FastAPI
//     (ARCHITECTURE.md §9 anti-pattern row "business logic in route handlers").
import { NextResponse } from "next/server";

import { ApiFetchError } from "@/lib/api-client";
import { createInvestigation, listInvestigations } from "@/lib/investigations";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const data = await listInvestigations();
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof ApiFetchError) {
      return NextResponse.json({ detail: err.detail }, { status: err.status });
    }
    throw err;
  }
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const data = await createInvestigation(body);
    return NextResponse.json(data, { status: 202 });
  } catch (err) {
    if (err instanceof ApiFetchError) {
      return NextResponse.json({ detail: err.detail }, { status: err.status });
    }
    throw err;
  }
}
