/**
 * Chat panel — the slide-from-right ceramic console (ported chat UI, ULTRON
 * transport). Owns the transcript, streams `/chat/stream`, and maps the SSE
 * frames (`token` / `tool_started` / `tool_completed` / `done` / `error`) onto
 * the rich `ChatMessage` shape the ported components render.
 *
 * Voice is ULTRON's own client (`voiceApi` + `VoiceClient`), the same pair the
 * command-core view uses; the composer's mic drives it.
 */
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Maximize2, Minimize2, AudioLines, X } from "lucide-react";

import { ApiError, streamChat, voiceApi } from "@/lib/api";
import { broadcastUnauthorized } from "@/lib/auth";
import { queryKeys } from "@/lib/queries";
import { VoiceClient } from "@/lib/voice";
import { cn } from "@/lib/utils";

import { Composer } from "./composer";
import { MessageBubble } from "./message-bubble";
import { SuggestionCards } from "./suggestion-cards";
import { makeId, type ChatMessage } from "./types";

export interface ChatPanelProps {
  open: boolean;
  onClose: () => void;
  /** A prompt pushed in from the console (submitted from the command bar). */
  seed?: { id: number; prompt: string } | null;
  /** Open hands-free live voice mode (the "chat read" trigger). */
  onStartLive?: () => void;
}

export function ChatPanel({ open, onClose, seed, onStartLive }: ChatPanelProps) {
  const queryClient = useQueryClient();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [maximized, setMaximized] = useState(false);
  const [listening, setListening] = useState(false);
  const [speakingId, setSpeakingId] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const controller = useRef<AbortController | null>(null);
  const activeIdRef = useRef<string | null>(null);
  const activeTextRef = useRef("");
  const streamingRef = useRef(false);
  const voiceRef = useRef<VoiceClient | null>(null);
  const lastSeedRef = useRef<number | null>(null);

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }, []);

  useEffect(() => {
    if (!stickRef.current) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const patch = useCallback((id: string, update: (m: ChatMessage) => ChatMessage) => {
    setMessages((current) => current.map((m) => (m.id === id ? update(m) : m)));
  }, []);

  const stopVoice = useCallback(() => {
    voiceRef.current?.stop();
    voiceRef.current = null;
    setListening(false);
  }, []);

  useEffect(() => () => {
    controller.current?.abort();
    voiceRef.current?.stop();
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  }, []);

  const submit = useCallback(
    async (prompt: string) => {
      const text = prompt.trim();
      if (!text || streamingRef.current) return;
      setError(null);
      stickRef.current = true;
      activeTextRef.current = "";
      setBusy(true);

      const userId = makeId();
      const replyId = makeId();
      activeIdRef.current = replyId;
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
              activeTextRef.current += chunk;
              patch(replyId, (m) => ({ ...m, text: m.text + chunk }));
              if (stickRef.current) scrollToBottom();
            },
            tool_started: ({ tool }) => {
              patch(replyId, (m) => ({
                ...m,
                toolSteps: [
                  ...(m.toolSteps ?? []),
                  { toolCallId: `${tool}-${makeId()}`, name: tool, state: "running" },
                ],
              }));
            },
            tool_completed: ({ tool, ok, output }) => {
              patch(replyId, (m) => ({
                ...m,
                toolSteps: (m.toolSteps ?? []).map((s) =>
                  s.name === tool && s.state === "running"
                    ? { ...s, state: ok ? "done" : "error", result: output != null ? String(output) : undefined }
                    : s,
                ),
              }));
            },
            done: ({ answer, model }) => {
              patch(replyId, (m) => ({
                ...m,
                text: m.text || answer,
                model,
                streaming: false,
              }));
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
          ac.signal,
        );
      } catch (err) {
        if ((err as { name?: string }).name === "AbortError") {
          patch(replyId, (m) => ({ ...m, streaming: false }));
        } else if (err instanceof ApiError && err.status === 401) {
          broadcastUnauthorized();
        } else {
          const message = err instanceof Error ? err.message : "stream failed";
          patch(replyId, (m) => ({ ...m, streaming: false, error: message }));
          setError(message);
        }
      } finally {
        patch(replyId, (m) => ({ ...m, streaming: false }));
        streamingRef.current = false;
        setBusy(false);
        controller.current = null;
      }
    },
    [patch, queryClient, scrollToBottom],
  );

  // A prompt seeded from the console opens the panel and fires immediately.
  useEffect(() => {
    if (!open || !seed) return;
    if (lastSeedRef.current === seed.id) return;
    lastSeedRef.current = seed.id;
    void submit(seed.prompt);
  }, [open, seed, submit]);

  const toggleVoice = useCallback(async () => {
    if (listening) {
      stopVoice();
      return;
    }
    setError(null);
    try {
      const engines = await voiceApi.engines();
      if (!engines.enabled || !engines.stt.available) {
        setError(engines.stt.reason ?? (engines.enabled ? "Speech-to-text is unavailable." : "Voice is disabled."));
        return;
      }
      const client = new VoiceClient({
        onState: (state) => {
          if (state === "closed") {
            setListening(false);
            voiceRef.current = null;
          }
        },
        onPartial: (partial) => setDraft(partial),
        onFinal: (final) => {
          setDraft("");
          void submit(final);
        },
        onAnswer: () => {},
        onError: (message) => {
          setError(message);
          setListening(false);
          voiceRef.current = null;
        },
      });
      voiceRef.current = client;
      client.unlock();
      setListening(true);
      await client.start();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start voice.");
      setListening(false);
      voiceRef.current = null;
    }
  }, [listening, stopVoice, submit]);

  const onSpeakMessage = useCallback((id: string, text: string) => {
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
  }, [speakingId]);

  const showSuggestions = messages.length === 0 && !busy && !error;

  const panelClass = useMemo(
    () => cn("chat-panel", maximized ? "chat-panel-max" : "chat-panel-docked"),
    [maximized],
  );

  return (
    <aside className={panelClass} aria-label="Ultron chat" aria-hidden={!open} data-open={open}>
      <div className="chat-panel-backdrop" onClick={onClose} aria-hidden="true" />
      <div className="chat-panel-surface">
        <header className="chat-panel-header">
          <div className="chat-panel-title">
            {busy ? (
              <span className="chat-panel-status">
                <span className="status-dot" />
                Working…
              </span>
            ) : (
              <span>Ultron ready.</span>
            )}
          </div>
          <div className="chat-panel-actions">
            {onStartLive ? (
              <button
                type="button"
                onClick={onStartLive}
                aria-label="Live voice mode"
                title="Live voice mode"
              >
                <AudioLines size={15} />
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => setMaximized((v) => !v)}
              aria-label={maximized ? "Restore" : "Maximize"}
              title={maximized ? "Restore" : "Maximize"}
            >
              {maximized ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
            </button>
            <button type="button" onClick={onClose} aria-label="Close chat" title="Close">
              <X size={16} />
            </button>
          </div>
        </header>

        <div ref={scrollRef} onScroll={onScroll} className="chat-panel-transcript">
          <div className="chat-transcript-inner">
            {showSuggestions ? (
              <div className="chat-empty animate-rise">
                <h2 className="chat-empty-title">Good to see you.</h2>
                <p className="chat-empty-sub">Chat with tools, memory, and voice. Try one of these:</p>
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
              <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-400">
                {error}
              </div>
            ) : null}
          </div>
        </div>

        <div className="chat-panel-composer">
          <Composer
            value={draft}
            onValueChange={setDraft}
            onSend={(text) => void submit(text)}
            disabled={busy}
            listening={listening}
            voiceEnabled
            onBeginVoice={() => void toggleVoice()}
            onEndVoice={stopVoice}
          />
        </div>
      </div>
    </aside>
  );
}
