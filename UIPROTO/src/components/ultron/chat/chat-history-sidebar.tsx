/**
 * Chat history rail — the slide-from-left drawer of past conversations. It
 * mirrors the chat panel's own geometry (same top/bottom insets, same fixed
 * height) but docks to the left and is deliberately narrower than a third of
 * the viewport. Presentation only: the session list and the active id are owned
 * by `ChatPanel`, so the drawer has no storage of its own and stays a pure
 * render of whatever the caller holds.
 */
"use client";

import { useEffect, useRef, useState } from "react";
import { MessageSquare, Plus, Trash2, X } from "lucide-react";

import { cn } from "@/lib/utils";

export interface ChatSessionSummary {
  id: string;
  title: string;
  preview: string;
  updatedAt: number;
  messageCount: number;
}

export interface ChatHistorySidebarProps {
  open: boolean;
  onClose: () => void;
  sessions: ChatSessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

function relativeTime(timestamp: number): string {
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function ChatHistorySidebar({
  open,
  onClose,
  sessions,
  activeId,
  onSelect,
  onNew,
  onDelete,
}: ChatHistorySidebarProps) {
  const [hoverId, setHoverId] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const el = listRef.current;
    if (el) el.scrollTop = 0;
  }, [open]);

  return (
    <aside
      className={cn("chat-history", open ? "chat-history-open" : "chat-history-closed")}
      aria-label="Chat history"
      aria-hidden={!open}
      data-open={open}
    >
      <header className="chat-history-header">
        <span className="chat-history-title">Conversations</span>
        <div className="chat-history-actions">
          <button type="button" onClick={onNew} aria-label="New chat" title="New chat">
            <Plus size={15} />
          </button>
          <button type="button" onClick={onClose} aria-label="Close history" title="Close">
            <X size={16} />
          </button>
        </div>
      </header>

      <div ref={listRef} className="chat-history-list">
        {sessions.length === 0 ? (
          <p className="chat-history-empty">No conversations yet.</p>
        ) : (
          sessions.map((session) => (
            <div
              key={session.id}
              className={cn("chat-history-item", session.id === activeId && "chat-history-item-active")}
              onMouseEnter={() => setHoverId(session.id)}
              onMouseLeave={() => setHoverId((current) => (current === session.id ? null : current))}
            >
              <button
                type="button"
                className="chat-history-item-main"
                onClick={() => onSelect(session.id)}
                title={session.title}
              >
                <MessageSquare size={14} className="chat-history-item-icon" />
                <span className="chat-history-item-body">
                  <span className="chat-history-item-title">{session.title}</span>
                  <span className="chat-history-item-preview">{session.preview || "Empty conversation"}</span>
                  <span className="chat-history-item-meta">
                    {relativeTime(session.updatedAt)} · {session.messageCount} msg
                  </span>
                </span>
              </button>
              {hoverId === session.id ? (
                <button
                  type="button"
                  className="chat-history-item-delete"
                  onClick={() => onDelete(session.id)}
                  aria-label="Delete conversation"
                  title="Delete"
                >
                  <Trash2 size={13} />
                </button>
              ) : null}
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
