// GET  /api/investigations/[id]/chat → history of chat turns (Plan 03-14 / CHAT-01).
// POST /api/investigations/[id]/chat → one turn, non-streaming JSON (CHAT-02).
//
// Thin Clerk-authed proxy: apiFetch pulls the JWT server-side and forwards it
// as Bearer to FastAPI (D-23). FastAPI's _load_user_investigation enforces row
// scoping — a user hitting another user's investigation id gets 404, which
// bubbles up here as ApiFetchError with status 404.
//
// Rejected alternatives:
//   - Shared HOF wrapper: 2 route handlers, explicit try/catch is cheaper than
//     indirection (same rationale as Plan 02-10 investigations routes).
//   - Streaming SSE: Phase 6-full scope; demo-lite keeps both paths JSON.
//   - Merging GET + POST into one endpoint: RESTful split keeps the client
//     useQuery cache for history distinct from the mutation.
import { NextResponse } from "next/server";

import { ApiFetchError } from "@/lib/api-client";
import { getChatHistory, postChatTurn } from "@/lib/investigations";

export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    return NextResponse.json(await getChatHistory(id));
  } catch (err) {
    if (err instanceof ApiFetchError) {
      return NextResponse.json({ detail: err.detail }, { status: err.status });
    }
    throw err;
  }
}

export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  let question: string;
  try {
    const body = (await req.json()) as { question?: unknown };
    if (typeof body?.question !== "string" || !body.question.trim()) {
      return NextResponse.json(
        { detail: "question_required" },
        { status: 422 },
      );
    }
    question = body.question;
  } catch {
    return NextResponse.json({ detail: "invalid_json" }, { status: 400 });
  }

  try {
    return NextResponse.json(await postChatTurn(id, question));
  } catch (err) {
    if (err instanceof ApiFetchError) {
      return NextResponse.json({ detail: err.detail }, { status: err.status });
    }
    throw err;
  }
}
