// POST /api/investigations/[id]/re-run → new investigation row with re_run_of FK
// (LIB-02 / D-18). Rate-limit enforced by FastAPI (Plan 02-09); 429 passes through.
import { NextResponse } from "next/server";

import { ApiFetchError } from "@/lib/api-client";
import { reRunInvestigation } from "@/lib/investigations";

export const dynamic = "force-dynamic";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    return NextResponse.json(await reRunInvestigation(id), { status: 202 });
  } catch (err) {
    if (err instanceof ApiFetchError) {
      return NextResponse.json({ detail: err.detail }, { status: err.status });
    }
    throw err;
  }
}
