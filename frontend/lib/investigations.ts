// Server-side typed helpers for FastAPI investigations endpoints.
// Called FROM Next.js route handlers and RSC page components — never from
// browser-running code (client components fetch the Next.js /api/* proxies,
// NOT FastAPI directly — D-23 thin-proxy pattern).
//
// Rejected alternatives:
//   - Client-side wrappers that call FastAPI directly: exposes the backend URL to
//     browser bundles and requires CORS. Proxy-via-Next.js is the D-23 contract.
//   - OpenAPI codegen: overkill for 7 endpoints; manual types are fine for v1.
//   - Merging with api-client.ts: keep apiFetch as the transport primitive; this
//     module gives named endpoint-level helpers so grep finds call sites cleanly.
import { apiFetch } from "@/lib/api-client";
import type {
  BriefResponse,
  ChatHistoryResponse,
  ChatTurnResponse,
  CreateInvestigationBody,
  CreateInvestigationResponse,
  InvestigationListItem,
  InvestigationListResponse,
  StatusResponse,
} from "@/lib/types";

export async function listInvestigations(): Promise<InvestigationListResponse> {
  return apiFetch<InvestigationListResponse>("/investigations");
}

export async function createInvestigation(
  body: CreateInvestigationBody,
): Promise<CreateInvestigationResponse> {
  return apiFetch<CreateInvestigationResponse>("/investigations", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getStatus(id: string): Promise<StatusResponse> {
  return apiFetch<StatusResponse>(`/investigations/${encodeURIComponent(id)}/status`);
}

export async function getBrief(id: string): Promise<BriefResponse> {
  return apiFetch<BriefResponse>(`/investigations/${encodeURIComponent(id)}/brief`);
}

export async function renameInvestigation(
  id: string,
  display_name: string,
): Promise<InvestigationListItem> {
  return apiFetch<InvestigationListItem>(`/investigations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ display_name }),
  });
}

export async function deleteInvestigation(id: string): Promise<void> {
  await apiFetch<void>(`/investigations/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function reRunInvestigation(id: string): Promise<CreateInvestigationResponse> {
  return apiFetch<CreateInvestigationResponse>(
    `/investigations/${encodeURIComponent(id)}/re-run`,
    { method: "POST" },
  );
}

// Phase 6-lite chat (Plan 03-14).
export async function getChatHistory(id: string): Promise<ChatHistoryResponse> {
  return apiFetch<ChatHistoryResponse>(
    `/investigations/${encodeURIComponent(id)}/chat`,
  );
}

export async function postChatTurn(
  id: string,
  question: string,
): Promise<ChatTurnResponse> {
  return apiFetch<ChatTurnResponse>(
    `/investigations/${encodeURIComponent(id)}/chat`,
    { method: "POST", body: JSON.stringify({ question }) },
  );
}
