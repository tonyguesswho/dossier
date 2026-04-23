// GET /api/investigations/[id]/brief → FastAPI GET brief (BRIEF-01)
//
// Backend returns 409 with {detail:"not_ready", status} while the pipeline is
// running; the 409 surfaces unchanged — the brief viewer (Plan 02-11) switches
// to RunningState on that signal.
import { NextResponse } from "next/server";

import { ApiFetchError } from "@/lib/api-client";
import { getBrief } from "@/lib/investigations";

export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    return NextResponse.json(await getBrief(id));
  } catch (err) {
    if (err instanceof ApiFetchError) {
      return NextResponse.json({ detail: err.detail }, { status: err.status });
    }
    throw err;
  }
}
