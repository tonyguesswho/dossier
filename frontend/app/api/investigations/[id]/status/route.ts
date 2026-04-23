// GET /api/investigations/[id]/status → FastAPI GET status (INVEST-03 / D-22 polling)
//
// Next.js 16 App Router: `params` is a Promise. Always `await params` before
// reading properties (enforced by the typecheck — passing a sync object errors).
import { NextResponse } from "next/server";

import { ApiFetchError } from "@/lib/api-client";
import { getStatus } from "@/lib/investigations";

export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    return NextResponse.json(await getStatus(id));
  } catch (err) {
    if (err instanceof ApiFetchError) {
      return NextResponse.json({ detail: err.detail }, { status: err.status });
    }
    throw err;
  }
}
