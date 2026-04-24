// Mirror of dossier.api.schemas from Plan 02-09 (Python Pydantic).
// Keep in sync: any breaking change to backend schemas requires an equivalent
// edit here. No runtime validation — these are type-only aliases.
//
// Rejected alternatives:
//   - OpenAPI codegen: overkill for 7 endpoints; manual types are fine for v1.
//   - Zod inference shared with backend: no shared runtime between TS/Python; parity
//     is enforced by the integration tests in Plan 02-09 plus this type mirror.

export type InvestigationStatus =
  | "queued"
  | "gathering"
  | "synthesizing"
  | "grounding"
  | "complete"
  | "failed";

export type InvestigationKind = "name" | "url";

export interface CreateInvestigationBody {
  kind: InvestigationKind;
  value: string;
  context_hint?: string;
}

export interface CreateInvestigationResponse {
  id: string;
  status: InvestigationStatus;
}

export interface InvestigationListItem {
  id: string;
  display_name: string;
  status: InvestigationStatus;
  started_at: string;
}

export interface InvestigationListResponse {
  items: InvestigationListItem[];
}

export interface StatusResponse {
  id: string;
  status: InvestigationStatus;
  display_name: string; // company label from input_ref (hint stripped); used for RunningState heading per UI-SPEC §4
  sources_count: number;
  claims_count: number;
  error: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface SourceListItem {
  id: string;
  url: string;
  source_kind: "web" | "github" | "crawl" | "news" | "deck_page" | "crunchbase";
}

export interface Scorecard {
  citation_precision: number;
  grounding_rate: number;
  total_claims: number;
  grounded_claims: number;
}

export interface BriefResponse {
  id: string;
  display_name: string;
  status: InvestigationStatus;
  brief_markdown: string;
  sources: SourceListItem[];
  started_at: string;
  completed_at: string | null;
  scorecard: Scorecard | null;
}

// Phase 6-lite chat (Plan 03-14).
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  cited_chunk_ids: string[];
  created_at: string;
}

export interface ChatHistoryResponse {
  investigation_id: string;
  messages: ChatMessage[];
}

export interface ChatTurnBody {
  question: string;
}

export interface ChatTurnResponse {
  answer: string;
  cited_chunk_ids: string[];
}
