/**
 * Dedicated chat workspace (§69, ported chat UI on ULTRON transport).
 *
 * The slide-over `ChatPanel` stays exactly as it is; this is a second, *full
 * area* chat surface that lives in its own left-rail tab. It reuses the same
 * transport (`streamChat`), the same message components, the same preference
 * key for the model pick, and the same server conversations — it is a different
 * place to chat, not a different chat stack.
 *
 * The transcript is client-side for the turn in flight and is persisted to a
 * server conversation id as soon as the first message is sent, so the history
 * rail in the slide-over panel sees the same thread.
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AudioLines, SquarePen, Trash2 } from "lucide-react";

import { ApiError, conversationsApi, streamChat } from "@/lib/api";
import { broadcastUnauthorized } from "@/lib/auth";
import { queryKeys } from "@/lib/queries";
import { getVoicePreferences } from "@/lib/voice-prefs";

import { Composer } from "./composer";
import { MessageBubble } from "./message-bubble";
import { SuggestionCards } from "./suggestion-cards";
import { makeId, type ChatMessage } from "./types";

export interface ChatWorkspaceProps {
  /** Open hands-free live voice mode (owned by the route, not duplicated here). */
  onStartLive?: () => void;
  /** True while the route's live voice session is running. */
  liveActive?: boolean;
}

export function ChatWorkspace({ onStartLive, liveActive = false }: ChatWorkspaceProps) {
  const queryClient = useQueryClient();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [lastModel, setLastModel] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const controller = useRef<AbortController | null>(null);
  const streamingRef = useRef(false);
  const conversationRef = useRef<string | null>(null);

  useEffect(() => {
    conversationRef.current = conversationId;
  }, [conversationId]);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  }, []);

  const patch = useCallback((id: string, change: (message: ChatMessage) => ChatMessage) => {
    setMessages((current) => current.map((message) => (message.id === id ? change(message) : message)));
  }, []);

  const stopGeneration = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    streamingRef.current = false;
    setBusy(false);
  }, []);

  const newChat = useCallback(() => {
    stopGeneration();
    setMessages([]);
    setDraft("");
    setError(null);
    setLastModel(null);
    setConversationId(null);
    conversationRef.current = null;
    stickRef.current = true;
  }, [stopGeneration]);

  useEffect(
    () => () => {
      controller.current?.abort();
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    },
    [],
  );

  const submit = useCallback(
    async (prompt: string) => {
      const text = prompt.trim();
      if (!text || streamingRef.current) return;
      setError(null);
      stickRef.current = true;
      setBusy(true);
      setDraft("");

      // Persist the turn to a server conversation so the existing history
      // surfaces see this thread too. A failed create simply leaves the turn
      // stateless rather than blocking the reply.
      let threadId = conversationRef.current;
      if (!threadId) {
        try {
          const created = await conversationsApi.create({});
          threadId = created.id;
          conversationRef.current = threadId;
          setConversationId(threadId);
        } catch {
          threadId = null;
        }
      }

      const userId = makeId();
      const replyId = makeId();
      setMessages((current) => [
        ...current,
        { id: userId, role: "user", text },
        { id: replyId, role: "assistant", text: "", streaming: true },
      ]);
      scrollToBottom();

      streamingRef.current = true;
      const ac = new AbortController();
      controller.current = ac;

      try {
        await streamChat(
          text,
          {
            token: ({ text: chunk }) => {
              patch(replyId, (message) => ({ ...message, text: message.text + chunk }));
              if (stickRef.current) scrollToBottom();
            },
            tool_started: ({ tool }) => {
              patch(replyId, (message) => ({
                ...message,
                toolSteps: [
                  ...(message.toolSteps ?? []),
                  { toolCallId: `${tool}-${makeId()}`, name: tool, state: "running" },
                ],
              }));
            },
            tool_completed: ({ tool, ok, output }) => {
              patch(replyId, (message) => ({
                ...message,
                toolSteps: (message.toolSteps ?? []).map((step) =>
                  step.name === tool && step.state === "running"
                    ? {
                        ...step,
                        state: ok ? "done" : "error",
                        result: output != null ? String(output) : undefined,
                      }
                    : step,
                ),
              }));
            },
            done: ({ answer, model, conversation_id }) => {
              patch(replyId, (message) => ({
                ...message,
                text: message.text || answer,
                model,
                streaming: false,
              }));
              if (model) setLastModel(model);
              if (conversation_id && conversation_id !== threadId) {
                conversationRef.current = conversation_id;
                setConversationId(conversation_id);
              }
              streamingRef.current = false;
              setBusy(false);
              void queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
              void queryClient.invalidateQueries({ queryKey: queryKeys.agents });
              scrollToBottom();
            },
            error: ({ message }) => {
              patch(replyId, (m) => ({ ...m, streaming: false, error: message }));
              setError(message);
              streamingRef.current = false;
              setBusy(false);
            },
          },
          {
            signal: ac.signal,
            model: getVoicePreferences().model || undefined,
            conversationId: threadId ?? undefined,
          },
        );
      } catch (err) {
        if ((err as { name?: string }).name === "AbortError") {
          patch(replyId, (message) => ({ ...message, streaming: false }));
        } else if (err instanceof ApiError && err.status === 401) {
          broadcastUnauthorized();
        } else {
          const message = err instanceof Error ? err.message : "stream failed";
          patch(replyId, (m) => ({ ...m, streaming: false, error: message }));
          setError(message);
        }
      } finally {
        patch(replyId, (message) => ({ ...message, streaming: false }));
        streamingRef.current = false;
        setBusy(false);
        controller.current = null;
      }
    },
    [patch, queryClient, scrollToBottom],
  );

  const onSpeakMessage = useCallback(
    (id: string, text: string) => {
      if (speakingId) {
        if ("speechSynthesis" in window) window.speechSynthesis.cancel();
        setSpeakingId(null);
        return;
      }
      if (!text.trim() || !("speechSynthesis" in window)) return;
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.onend = () => setSpeakingId(null);
      setSpeakingId(id);
      window.speechSynthesis.speak(utterance);
    },
    [speakingId],
  );

  const showSuggestions = messages.length === 0 && !busy && !error;
  const empty = messages.length === 0;

  return (
    <section className="chat-workspace" aria-label="Dedicated chat">
      <header className="chat-workspace-header">
        <div className="chat-workspace-title">
          <span className="status-dot" />
          <strong>CHAT</strong>
          <span className="chat-workspace-sub">
            {busy ? "Responding…" : lastModel ?? "Ultron ready"}
          </span>
        </div>
        <div className="chat-workspace-actions">
          {onStartLive ? (
            <button
              type="button"
              onClick={onStartLive}
              title={liveActive ? "Stop live voice" : "Live voice mode"}
              aria-pressed={liveActive}
            >
              <AudioLines size={15} />
            </button>
          ) : null}
          <button type="button" onClick={newChat} title="New chat">
            <SquarePen size={15} />
          </button>
          <button
            type="button"
            onClick={newChat}
            title="Clear transcript"
            disabled={empty && !error}
          >
            <Trash2 size={15} />
          </button>
        </div>
      </header>

      <div ref={scrollRef} onScroll={onScroll} className="chat-workspace-transcript">
        <div className="chat-transcript-inner">
          {showSuggestions ? (
            <div className="chat-empty animate-rise">
              <h2 className="chat-empty-title">Dedicated chat.</h2>
              <p className="chat-empty-sub">
                A full-area thread, separate from the slide-over panel. Same tools, memory and model.
              </p>
              <SuggestionCards visible onPick={(prompt) => void submit(prompt)} />
            </div>
          ) : null}

          {messages.map((message) => (
            <MessageBubble
              key={message.id}
              message={message}
              onSpeak={onSpeakMessage}
              isSpeaking={speakingId === message.id}
            />
          ))}

          {error ? (
            <div className="chat-workspace-error" role="alert">
              {error}
            </div>
          ) : null}
        </div>
      </div>

      <div className="chat-workspace-composer">
        <Composer
          value={draft}
          onValueChange={setDraft}
          onSend={(text) => void submit(text)}
          busy={busy}
          onStop={stopGeneration}
          voiceEnabled={false}
          placeholder="Message Ultron… (Enter to send)"
        />
        <div className="chat-workspace-foot">
          <span>{conversationId ? "Thread saved to history" : "New thread"}</span>
          <span>{getVoicePreferences().model || "Default model"}</span>
        </div>
      </div>
    </section>
  );
}
