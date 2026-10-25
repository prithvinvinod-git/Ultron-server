/**
 * Conversations panel (T506, §22).
 *
 * Shows the caller's active threads most-recent-first and lets them
 * rename or delete a thread. Opening one hands off to the chat surface
 * (/chat) — the same entry point the command bar uses.
 *
 * Design follows the Arc Reactor theme: status dots indicate activity,
 * clean typography, and intentional spacing for command-center clarity.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Check, Edit, ListChecks, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { conversationsApi } from "@/lib/api";
import type { Conversation } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { useWorkspace } from "@/lib/workspace";

function shortTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("en-GB", { hour12: false });
}

export function ConversationsPanel() {
  const queryClient = useQueryClient();
  const { select } = useWorkspace();

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.conversations,
    queryFn: conversationsApi.list,
    refetchInterval: 15000,
  });

  const convs = data ?? [];

  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [renameTitle, setRenameTitle] = useState("");

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      conversationsApi.rename(id, title),
    onSuccess: () => {
      void refetch();
      setRenaming(null);
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : "rename failed");
      setRenaming(null);
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => conversationsApi.remove(id),
    onSuccess: () => {
      void refetch();
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : "delete failed");
    },
  });

  function handleRename(id: string, title: string) {
    setRenaming({ id, title });
    setRenameTitle(title);
  }

  function cancelRename() {
    setRenaming(null);
    setRenameTitle("");
  }

  function confirmRename() {
    if (!renaming) return;
    void rename.mutate({ id: renaming.id, title: renameTitle });
  }

  return (
    <section className="hud-panel">
      <div className="panel-heading">
        <h2>CONVERSATIONS</h2>
        <Button
          variant="ghost"
          size="sm"
          className="panel-reload"
          onClick={() => void refetch()}
          disabled={isLoading}
          title="Refresh conversations"
        >
          <ListChecks size={12} />
        </Button>
      </div>

      {error ? (
        <div className="panel-empty">conversations unavailable</div>
      ) : isLoading && !convs.length ? (
        <div className="panel-empty">loading…</div>
      ) : !convs.length ? (
        <div className="panel-empty">no conversations</div>
      ) : (
        <div className="agent-list">
          {convs.map((conv) => (
            <div key={conv.id} className="agent-row">
              <span className={`status-dot${conv.status === "active" ? "" : " inactive"}`} />
              <div>
                {renaming?.id === conv.id ? (
                  <>
                    <input
                      type="text"
                      value={renameTitle}
                      onChange={(e) => setRenameTitle(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") confirmRename();
                        if (e.key === "Escape") cancelRename();
                      }}
                      aria-label="Rename conversation"
                      className="conv-rename-input"
                    />
                    <div className="conv-actions">
                      <Button variant="ghost" size="sm" onClick={cancelRename}>
                        <X size={16} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={confirmRename}
                        disabled={!renameTitle.trim()}
                      >
                        <Check size={16} />
                      </Button>
                    </div>
                  </>
                ) : (
                  <>
                    <strong>{conv.title ?? "Untitled conversation"}</strong>
                    <small>{shortTime(conv.updated_at)} • {conv.status}</small>
                    <div className="conv-actions">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => select({ kind: "conversation", id: conv.id })}
                        aria-label="Open conversation"
                      >
                        <ListChecks size={16} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleRename(conv.id, conv.title ?? "")}
                        aria-label="Rename conversation"
                      >
                        <Edit size={16} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void remove.mutate(conv.id)}
                        aria-label="Delete conversation"
                      >
                        <Trash2 size={16} />
                      </Button>
                    </div>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="panel-foot">
        <span>{convs.length} threads</span>
        <span>MESSAGE LOG</span>
      </div>
    </section>
  );
}
