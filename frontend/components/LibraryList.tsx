"use client";

// Library list — UI-SPEC §2 (dense rows newest first, D-17) + §6 (delete modal).
// Polling via TanStack Query (CONTEXT.md D-22); list-level refetch at 5s.
// Per-investigation status polling at 2s lives in Plan 02-11's running-state page.
//
// Rejected alternatives (from UI-SPEC §2):
//   - Card grid: less dense; UI-SPEC locks a single-row list.
//   - Pagination: typical VC use is tens of investigations, not thousands.
//   - Soft delete: D-19 hard delete only (CASCADE).
//   - Modal rename: inline rename is cheaper for a reversible text edit.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MoreHorizontal, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { InvestigationListItem, InvestigationListResponse } from "@/lib/types";

const LIST_REFETCH_MS = 5_000;

function relativeTime(iso: string): string {
  const ms = Date.now() - Date.parse(iso);
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

async function fetchList(): Promise<InvestigationListResponse> {
  const res = await fetch("/api/investigations", { cache: "no-store" });
  if (!res.ok) throw new Error(`list failed: ${res.status}`);
  return res.json();
}

export function LibraryList() {
  const router = useRouter();
  const qc = useQueryClient();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["investigations"],
    queryFn: fetchList,
    refetchInterval: LIST_REFETCH_MS,
  });

  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<InvestigationListItem | null>(null);

  const renameMutation = useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string }) => {
      const res = await fetch(`/api/investigations/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: name }),
      });
      if (!res.ok) throw new Error("rename failed");
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["investigations"] }),
    onError: () => toast.error("Rename failed. Please try again."),
  });

  const reRunMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`/api/investigations/${id}/re-run`, { method: "POST" });
      if (!res.ok) throw new Error("re-run failed");
      return res.json();
    },
    onSuccess: (_, id) => {
      const item = data?.items.find((i) => i.id === id);
      toast.success(`Re-run queued for ${item?.display_name ?? "investigation"}`);
      qc.invalidateQueries({ queryKey: ["investigations"] });
    },
    onError: () => toast.error("Re-run failed. Please try again."),
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`/api/investigations/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("delete failed");
    },
    onMutate: async (id) => {
      // Optimistic update: snapshot current cache, remove the row locally, then let
      // onError restore the snapshot if the network call fails (T-02-10-05 mitigation).
      await qc.cancelQueries({ queryKey: ["investigations"] });
      const prev = qc.getQueryData<InvestigationListResponse>(["investigations"]);
      qc.setQueryData<InvestigationListResponse>(["investigations"], (old) =>
        old ? { items: old.items.filter((i) => i.id !== id) } : old,
      );
      return { prev };
    },
    onError: (_err, _id, ctx) => {
      if (ctx?.prev) qc.setQueryData(["investigations"], ctx.prev);
      toast.error("Delete failed. Please try again.");
    },
    onSuccess: () => {
      toast.success("Investigation deleted");
      qc.invalidateQueries({ queryKey: ["investigations"] });
    },
  });

  if (isLoading) {
    return (
      <div className="flex flex-col gap-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-[52px] w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <p className="text-destructive text-[13px]">
        Couldn&apos;t load investigations. Refresh to try again.
      </p>
    );
  }

  const items = data?.items ?? [];

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center text-center gap-4 py-20">
        <h2 className="text-[30px] font-semibold">No investigations yet</h2>
        <p className="text-[15px] text-muted-foreground max-w-md">
          Run your first investigation to get a one-page brief in 2–4 minutes.
        </p>
        <Button asChild style={{ backgroundColor: "#4f46e5", color: "white" }}>
          <Link href="/investigations/new">Investigate</Link>
        </Button>
      </div>
    );
  }

  return (
    <>
      <ul role="list" className="divide-y divide-border rounded-lg border bg-card">
        {items.map((item) => (
          <li
            key={item.id}
            role="row"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter") router.push(`/investigations/${item.id}`);
            }}
            className={cn(
              "flex items-center gap-4 px-4 h-[52px] hover:bg-stone-50 cursor-pointer",
            )}
          >
            {renameId === item.id ? (
              <Input
                autoFocus
                value={renameValue}
                maxLength={200}
                onChange={(e) => setRenameValue(e.target.value)}
                onBlur={() => {
                  if (renameValue.trim() && renameValue.trim() !== item.display_name) {
                    renameMutation.mutate({ id: item.id, name: renameValue.trim() });
                  }
                  setRenameId(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur();
                  if (e.key === "Escape") setRenameId(null);
                }}
                className="flex-1"
              />
            ) : (
              <button
                type="button"
                className="flex-1 truncate text-left text-[15px] font-semibold"
                onClick={() => router.push(`/investigations/${item.id}`)}
              >
                {item.display_name}
              </button>
            )}
            <StatusPill status={item.status} />
            <span className="hidden sm:inline text-[13px] text-muted-foreground whitespace-nowrap">
              {relativeTime(item.started_at)}
            </span>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`More options for ${item.display_name}`}
                  className="min-h-[44px] min-w-[44px]"
                >
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => router.push(`/investigations/${item.id}`)}>
                  View brief
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => reRunMutation.mutate(item.id)}>
                  Re-run
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={() => {
                    setRenameValue(item.display_name);
                    setRenameId(item.id);
                  }}
                >
                  Rename
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => setDeleteTarget(item)}
                  className="text-destructive"
                >
                  <Trash2 className="h-4 w-4 mr-2" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </li>
        ))}
      </ul>

      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete investigation?</DialogTitle>
            <DialogDescription>
              This will permanently delete the brief for {deleteTarget?.display_name} and all its
              sources. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setDeleteTarget(null)} autoFocus>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (deleteTarget) deleteMutation.mutate(deleteTarget.id);
                setDeleteTarget(null);
              }}
              className="min-w-[80px]"
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
