// POST /api/investigations/upload → FastAPI POST /investigations/upload (INPUT-03).
//
// Thin Clerk-authed proxy — same contract as /api/investigations/route.ts, but
// the body is multipart/form-data (PDF file + optional context_hint) and must
// be forwarded verbatim. apiFetch() is JSON-only and auto-sets Content-Type:
// application/json, so we cannot reuse it here; hand-rolling the Clerk JWT
// forward is the smaller change.
//
// Rejected alternatives:
//   - Have apiFetch skip Content-Type when body is FormData: spreads multipart
//     awareness into the shared transport primitive; cleaner to keep the one
//     multipart route handler self-contained.
//   - Parse the file into a Buffer and re-serialize: wastes memory on large
//     decks; Node 20's fetch will stream the incoming Request body to FastAPI
//     through the ReadableStream fine.
//   - Put the Clerk token on the request as a cookie: FastAPI require_clerk_user_id
//     (Plan 02-03) only reads Authorization Bearer; matching that contract here.
import { auth } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

// Match the Python-side cap so the frontend fails fast without streaming 50 MB
// into Node then getting a 413 from FastAPI. 17 MB is the DECK_MAX_BYTES const
// in backend/src/dossier/api/routes/investigations.py.
const MAX_UPLOAD_BYTES = 17 * 1024 * 1024;

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export async function POST(req: Request) {
  const { getToken } = await auth();
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ detail: "unauthorized" }, { status: 401 });
  }

  // Content-Length is advisory (not always present for streamed bodies) but
  // when set it's a cheap pre-check. FastAPI still enforces DECK_MAX_BYTES
  // after reading.
  const contentLength = req.headers.get("content-length");
  if (contentLength && Number(contentLength) > MAX_UPLOAD_BYTES) {
    return NextResponse.json({ detail: "pdf_too_large" }, { status: 413 });
  }

  // Parse once to re-emit as a fresh FormData to FastAPI. We can't just stream
  // req.body because Node's fetch strips the multipart boundary on re-serialize
  // unless we reconstruct the FormData.
  let incoming: FormData;
  try {
    incoming = await req.formData();
  } catch {
    return NextResponse.json({ detail: "invalid_multipart" }, { status: 400 });
  }

  const forward = new FormData();
  const file = incoming.get("file");
  if (!(file instanceof File)) {
    return NextResponse.json({ detail: "file_required" }, { status: 400 });
  }
  forward.set("file", file, file.name);

  const contextHint = incoming.get("context_hint");
  if (typeof contextHint === "string" && contextHint.trim().length > 0) {
    forward.set("context_hint", contextHint);
  }

  const backendRes = await fetch(`${BACKEND_URL}/investigations/upload`, {
    method: "POST",
    headers: {
      // Do NOT set Content-Type — fetch + FormData set multipart/form-data with
      // the correct boundary automatically. Setting it manually here would
      // strip the boundary and FastAPI would 400 on the malformed body.
      Authorization: `Bearer ${token}`,
    },
    body: forward,
    cache: "no-store",
  });

  // Passthrough the backend's JSON and status verbatim so the form can rely
  // on the same status-code vocabulary (202/413/422/429) it already handles.
  const payload = await backendRes.json().catch(() => ({ detail: backendRes.statusText }));
  return NextResponse.json(payload, { status: backendRes.status });
}
