"use client";

// ChatPane — Phase 6-lite basic RAG chat UI (Plan 03-14 / CHAT-01 / CHAT-02).
//
// Non-streaming JSON round-trip per turn. History lives in the backend
// chat_messages table; this component loads it on mount via TanStack Query
// (matches the 2s-poll / dependent-query pattern from Plan 02-11) and
// appends each new turn optimistically.
//
// Markdown rendering: react-markdown + remark-gfm, same stack as BriefViewer.
// Inline citation links are already resolved server-side in chat.resolve_citations
// — the backend sends `([source](https://...))` markdown; react-markdown turns
// that into an anchor. NO rehype-raw → script tags render as literal text
// (T-02-11-01 defense reused).
//
// Rejected alternatives (CLAUDE.md D-20 lock + 03-14-PLAN.md scope_note):
//   - Vercel AI SDK / AI Elements / @ai-sdk/react useChat: locked out project-wide.
//   - SSE streaming via @microsoft/fetch-event-source: Phase 6-full scope.
//   - Local-only message state without DB reload: history survives page reload
//     is an explicit success criterion.
//   - Shared "useInvestigation" hook: two useQueries here already work fine
//     and parallel the page's pattern; abstraction is premature at this scale.
//   - Separate mutation + manual cache invalidate: appending optimistically and
//     on-success is cheaper to read than a queryClient.setQueryData dance, and
//     the GET /chat is cheap enough that on page navigation a fresh fetch wins.
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Send } from "lucide-react";
import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { ChatHistoryResponse, ChatMessage, ChatTurnResponse } from "@/lib/types";

async function fetchHistory(id: string): Promise<ChatHistoryResponse> {
  const res = await fetch(`/api/investigations/${id}/chat`, { cache: "no-store" });
  if (!res.ok) throw new Error(`chat history failed: ${res.status}`);
  return res.json();
}

async function sendTurn(
  id: string,
  question: string,
): Promise<ChatTurnResponse> {
  const res = await fetch(`/api/investigations/${id}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON */
    }
    throw new Error(detail);
  }
  return res.json();
}

function detailToMessage(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (msg === "not_ready") {
    return "This investigation isn't ready for chat yet.";
  }
  if (msg === "not_found") {
    return "This investigation was deleted.";
  }
  if (msg === "rate_limited") {
    return "You've hit the daily limit. Try again tomorrow.";
  }
  if (msg === "guardrail_rejected") {
    return "That question was rejected. Please rephrase.";
  }
  return "Something went wrong. Please try again.";
}

export function ChatPane({ investigationId }: { investigationId: string }) {
  const [draft, setDraft] = useState("");
  const [optimisticMessages, setOptimisticMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const historyQuery = useQuery<ChatHistoryResponse>({
    queryKey: ["investigation", investigationId, "chat"],
    queryFn: () => fetchHistory(investigationId),
  });

  const turnMutation = useMutation<ChatTurnResponse, Error, string>({
    mutationFn: (question: string) => sendTurn(investigationId, question),
  });

  // Merged message list = server history + any locally-appended turns the
  // server hasn't confirmed yet. On successful mutation we invalidate + refetch
  // the history, at which point the optimistic entries are discarded.
  const messages: ChatMessage[] = [
    ...(historyQuery.data?.messages ?? []),
    ...optimisticMessages,
  ];

  // Auto-scroll the pane to bottom when a new message lands.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, turnMutation.isPending]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const question = draft.trim();
    if (!question || turnMutation.isPending) return;
    setError(null);

    const now = new Date().toISOString();
    const userTurn: ChatMessage = {
      role: "user",
      content: question,
      cited_chunk_ids: [],
      created_at: now,
    };
    // Optimistically append the user turn so it renders immediately.
    setOptimisticMessages((prev) => [...prev, userTurn]);
    setDraft("");

    try {
      const result = await turnMutation.mutateAsync(question);
      const assistantTurn: ChatMessage = {
        role: "assistant",
        content: result.answer,
        cited_chunk_ids: result.cited_chunk_ids,
        created_at: new Date().toISOString(),
      };
      setOptimisticMessages((prev) => [...prev, assistantTurn]);
      // Refetch authoritative history; on success the optimistic entries are
      // replaced by the server copies (same content, but with DB-generated
      // created_at values).
      const refreshed = await historyQuery.refetch();
      if (refreshed.data) {
        setOptimisticMessages([]);
      }
    } catch (err) {
      // Roll back the optimistic user turn on failure.
      setOptimisticMessages((prev) => prev.filter((m) => m !== userTurn));
      setError(detailToMessage(err));
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Cmd/Ctrl+Enter or Shift+Enter sends; plain Enter inserts a newline
    // (matches Textarea-first UX — chat box, not a URL bar).
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void onSubmit(e as unknown as React.FormEvent);
    }
  };

  return (
    <section className="max-w-3xl mx-auto px-4 pb-12" data-no-print>
      <div className="border-t border-border pt-8">
        <h2 className="font-display font-normal text-[1.5rem] leading-tight mb-1">Ask a follow-up</h2>
        <p className="text-[13px] text-muted-foreground mb-4">
          Grounded in the sources above. Cmd/Ctrl+Enter to send.
        </p>

        <div
          ref={scrollRef}
          aria-live="polite"
          aria-relevant="additions"
          className="flex flex-col gap-5 max-h-[420px] overflow-y-auto pr-1 mb-3"
        >
          {historyQuery.isLoading && messages.length === 0 && (
            <p className="text-[13px] text-muted-foreground">Loading history…</p>
          )}
          {!historyQuery.isLoading && messages.length === 0 && (
            <p className="text-[13px] text-muted-foreground">
              No messages yet. Ask anything about this investigation.
            </p>
          )}
          {messages.map((msg, i) => (
            <MessageBubble key={`${msg.created_at}-${i}`} message={msg} />
          ))}
          {turnMutation.isPending && (
            <div className="flex items-center gap-2 text-[13px] text-muted-foreground pl-3">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Thinking…
            </div>
          )}
        </div>

        {error && (
          <p className="text-[13px] text-destructive mb-2" role="alert">
            {error}
          </p>
        )}

        <form onSubmit={onSubmit} className="flex flex-col gap-2">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="e.g. What's the founder's background?"
            rows={2}
            maxLength={1000}
            disabled={turnMutation.isPending}
            aria-label="Chat question"
          />
          <div className="flex justify-end">
            <Button
              type="submit"
              disabled={!draft.trim() || turnMutation.isPending}
            >
              {turnMutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                  Sending…
                </>
              ) : (
                <>
                  <Send className="h-4 w-4 mr-2" />
                  Send
                </>
              )}
            </Button>
          </div>
        </form>
      </div>
    </section>
  );
}

const MessageBubble = memo(function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex flex-col gap-1 ${isUser ? "items-end" : "items-start"}`}>
      <span className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider px-1">
        {isUser ? "You" : "Dossier"}
      </span>
      {isUser ? (
        // User text: plain whitespace-preserved, not markdown — avoids XSS
        // surface and prevents [S:...] grounding markers from rendering.
        <div className="max-w-[80%] bg-primary text-primary-foreground rounded px-4 py-2.5 text-[14px]">
          <p className="whitespace-pre-wrap">{message.content}</p>
        </div>
      ) : (
        <div className="w-full border-l-2 border-primary/30 pl-4">
          <div className="prose prose-sm max-w-none text-[14px]">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content}
            </ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
});
