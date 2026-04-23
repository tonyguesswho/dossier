// PATCH rename + DELETE hard-delete (LIB-03, D-19)
//
// Both methods live in one file because they share a dynamic-segment path. NO
// business logic — user-scoping + injection-substring checks happen in the
// FastAPI RenameInvestigationBody validator (Plan 02-09).
import { NextResponse } from "next/server";

import { ApiFetchError } from "@/lib/api-client";
import { deleteInvestigation, renameInvestigation } from "@/lib/investigations";

export const dynamic = "force-dynamic";

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const body = await req.json();
    return NextResponse.json(await renameInvestigation(id, body.display_name));
  } catch (err) {
    if (err instanceof ApiFetchError) {
      return NextResponse.json({ detail: err.detail }, { status: err.status });
    }
    throw err;
  }
}

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    await deleteInvestigation(id);
    return new NextResponse(null, { status: 204 });
  } catch (err) {
    if (err instanceof ApiFetchError) {
      return NextResponse.json({ detail: err.detail }, { status: err.status });
    }
    throw err;
  }
}
