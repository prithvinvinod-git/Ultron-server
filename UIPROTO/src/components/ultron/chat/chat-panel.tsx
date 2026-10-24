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
import { History, Maximize2, Minimize2, AudioLines, X } from "lucide-react";

import { ApiError, conversationsApi, streamChat, voiceApi } from "@/lib/api";
import type { ConversationDetail, ConversationMessage } from "@/lib/api";
import { broadcastUnauthorized } from "@/lib/auth";
import { queryKeys } from "@/lib/queries";
import { VoiceClient } from "@/lib/voice";
import { getVoicePreferences, checkSttChoice, resolveSttChoice } from "@/lib/voice-prefs";
import { cn } from "@/lib/utils";

import { Composer } from "./composer";
import { MessageBubble } from "./message-bubble";
import { SuggestionCards } from "./suggestion-cards";
import { ChatHistorySidebar, type ChatSessionSummary } from "./chat-history-sidebar";
import { makeId, type ChatMessage } from "./types";

export interface ChatPanelProps {
    open: boolean;
    onClose: () => void;
    /** A prompt pushed in from the console (submitted from the command bar). */
    seed?: { id: number; prompt: string } | null;
    /** Open hands-free live voice mode (the "chat read" trigger). */
    onStartLive?: () => void;
}

/** A conversation held client-side: one transcript, its own id and title. */
interface ChatSession {
    id: string;
    serverId: string | null;
    title: string;
    messages: ChatMessage[];
    messagesLoaded: boolean;
    createdAt: number;
    updatedAt: number;
}

function titleFromMessages(messages: ChatMessage[]): string {
    const firstUser = messages.find((m) => m.role === "user" && m.text.trim());
    if (!firstUser) return "New conversation";
    const text = firstUser.text.trim().replace(/\s+/g, " ");
    return text.length > 42 ? `${text.slice(0, 42)}…` : text;
}

function previewFromMessages(messages: ChatMessage[]): string {
    const lastAssistant = [...messages]
        .reverse()
        .find((m) => m.role === "assistant" && m.text.trim());
    const source = lastAssistant ?? [...messages].reverse().find((m) => m.text.trim());
    if (!source) return "";
    const text = source.text.trim().replace(/\s+/g, " ");
    return text.length > 60 ? `${text.slice(0, 60)}…` : text;
}

function newSession(): ChatSession {
    const now = Date.now();
    return {
        id: makeId(),
        serverId: null,
        title: "New conversation",
        messages: [],
        messagesLoaded: false,
        createdAt: now,
        updatedAt: now,
    };
}

/** Map a server transcript row onto the UI message shape. */
function serverMessageToUI(m: ConversationMessage): ChatMessage {
    return {
        id: m.id,
        role: m.role === "user" ? "user" : "assistant",
        text: m.content,
        model: m.model ?? undefined,
    };
}

export function ChatPanel({ open, onClose, seed, onStartLive }: ChatPanelProps) {
    const queryClient = useQueryClient();
    const [sessions, setSessions] = useState<ChatSession[]>(() => [newSession()]);
    const [activeSessionId, setActiveSessionId] = useState<string>(() => "");
    const [historyOpen, setHistoryOpen] = useState(true);

    // The history rail is a companion to the panel: it slides in with the chat
    // and can be collapsed from the header, then reappears the next time the
    // chat opens.
    useEffect(() => {
        if (open) setHistoryOpen(true);
    }, [open]);

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

    // The initial session has an empty id until the effect below pins it, so the
    // first render never reads a session that does not exist yet.
    useEffect(() => {
        if (!activeSessionId && sessions[0]) setActiveSessionId(sessions[0].id);
    }, [activeSessionId, sessions]);

    // Load server conversations on open
    const loadConversations = useCallback(async () => {
        try {
            const serverConvs = await conversationsApi.list();
            const loaded: ChatSession[] = serverConvs.map((c) => ({
                id: c.id,
                serverId: c.id,
                title: c.title ?? "Untitled",
                messages: [],
                messagesLoaded: false,
                createdAt: new Date(c.created_at).getTime(),
                updatedAt: new Date(c.updated_at).getTime(),
            }));
            if (loaded.length > 0) {
                setSessions(loaded);
            }
        } catch {
            // Anonymous/offline — keep client-side sessions only
        }
    }, []);

    // Load transcript for a session. Memoised so its identity is stable: the
    // effect that loads on open and the select handler both depend on it, and a
    // function rebuilt every render would re-run those effects forever.
    const loadTranscript = useCallback(async (sessionId: string) => {
        try {
            const detail = await conversationsApi.get(sessionId);
            const messages = detail.messages
                .filter((m) => m.role === "user" || m.role === "assistant")
                .map(serverMessageToUI);
            setSessions((current) =>
                current.map((session) =>
                    session.id === sessionId
                        ? { ...session, messages, messagesLoaded: true }
                        : session,
                ),
            );
        } catch {
            // Failed to load — leave empty
        }
    }, []);

    // Load on open
    useEffect(() => {
        if (open) void loadConversations();
    }, [open, loadConversations]);

    const activeSession = useMemo(
        () => sessions.find((s) => s.id === activeSessionId) ?? sessions[0],
        [sessions, activeSessionId],
    );
    const messages = activeSession?.messages ?? [];

    const setMessages = useCallback(
        (update: (current: ChatMessage[]) => ChatMessage[]) => {
            setSessions((current) =>
                current.map((session) =>
                    session.id === activeSession?.id
                        ? { ...session, messages: update(session.messages), updatedAt: Date.now() }
                        : session,
                ),
            );
        },
        [activeSession?.id],
    );

    const historySummaries = useMemo<ChatSessionSummary[]>(
        () =>
            [...sessions]
                .sort((a, b) => b.updatedAt - a.updatedAt)
                .map((session) => ({
                    id: session.id,
                    title: session.title,
                    preview: previewFromMessages(session.messages),
                    updatedAt: session.updatedAt,
                    messageCount: session.messages.length,
                })),
        [sessions],
    );

    const startNewChat = useCallback(() => {
        const session = newSession();
        setSessions((current) => [session, ...current]);
        setActiveSessionId(session.id);
        setError(null);
        setDraft("");
        setHistoryOpen(false);
    }, []);

    const selectSession = useCallback(
        (id: string) => {
            const session = sessions.find((s) => s.id === id);
            if (session?.serverId && !session.messagesLoaded) {
                loadTranscript(id);
            }
            setActiveSessionId(id);
            setError(null);
            setHistoryOpen(false);
        },
        [sessions, loadTranscript],
    );

    const deleteSession = useCallback(
        (id: string) => {
            const session = sessions.find((s) => s.id === id);
            // The server conversation is addressed by its own id, which is not
            // the client session id once a session has been created remotely.
            if (session?.serverId) {
                conversationsApi.remove(session.serverId).catch(() => {});
            }
            setSessions((current) => {
                const remaining = current.filter((session) => session.id !== id);
                const next = remaining.length > 0 ? remaining : [newSession()];
                setActiveSessionId((active) => (active === id ? next[0]!.id : active));
                return next;
            });
        },
        [sessions],
    );

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

    // While a response streams, keep the transcript pinned to the newest token as
    // long as the reader has not scrolled away.
    useEffect(() => {
        if (!busy) return;
        const el = scrollRef.current;
        if (!el) return;
        const id = window.setInterval(() => {
            if (stickRef.current) el.scrollTop = el.scrollHeight;
        }, 120);
        return () => window.clearInterval(id);
    }, [busy]);

    const onScroll = () => {
        const el = scrollRef.current;
        if (!el) return;
        stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };

    const patch = useCallback(
        (id: string, update: (m: ChatMessage) => ChatMessage) => {
            setMessages((current) => current.map((m) => (m.id === id ? update(m) : m)));
        },
        [setMessages],
    );

    const stopVoice = useCallback(() => {
        voiceRef.current?.stop();
        voiceRef.current = null;
        setListening(false);
    }, []);

    const stopGeneration = useCallback(() => {
        controller.current?.abort();
        controller.current = null;
        streamingRef.current = false;
        setBusy(false);
    }, []);

    useEffect(
        () => () => {
            controller.current?.abort();
            voiceRef.current?.stop();
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
            activeTextRef.current = "";
            setBusy(true);
            setDraft("");

            // Ensure we have a server conversation id
            let conversationId = activeSession?.serverId ?? null;
            if (!conversationId) {
                try {
                    const created = await conversationsApi.create({});
                    conversationId = created.id;
                    setSessions((current) =>
                        current.map((session) =>
                            session.id === activeSession?.id
                                ? { ...session, serverId: conversationId }
                                : session,
                        ),
                    );
                } catch {
                    // Could not create — proceed stateless
                }
            }

            const userId = makeId();
            const replyId = makeId();
            activeIdRef.current = replyId;
            setMessages((current) => {
                const next = [
                    ...current,
                    { id: userId, role: "user" as const, text },
                    { id: replyId, role: "assistant" as const, text: "", streaming: true },
                ];
                return next;
            });
            // The session list shows the opening line, so title it on first send.
            setSessions((current) =>
                current.map((session) =>
                    session.id === activeSession?.id && session.title === "New conversation"
                        ? {
                              ...session,
                              title: titleFromMessages([{ id: userId, role: "user", text }]),
                          }
                        : session,
                ),
            );
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
                                    {
                                        toolCallId: `${tool}-${makeId()}`,
                                        name: tool,
                                        state: "running",
                                    },
                                ],
                            }));
                        },
                        tool_completed: ({ tool, ok, output }) => {
                            patch(replyId, (m) => ({
                                ...m,
                                toolSteps: (m.toolSteps ?? []).map((s) =>
                                    s.name === tool && s.state === "running"
                                        ? {
                                              ...s,
                                              state: ok ? "done" : "error",
                                              result: output != null ? String(output) : undefined,
                                          }
                                        : s,
                                ),
                            }));
                        },
                        done: ({ answer, model, conversation_id }) => {
                            patch(replyId, (m) => ({
                                ...m,
                                text: m.text || answer,
                                model,
                                streaming: false,
                            }));
                            // Capture server conversation id if returned
                            if (conversation_id && conversationId !== conversation_id) {
                                setSessions((current) =>
                                    current.map((session) =>
                                        session.id === activeSession?.id
                                            ? { ...session, serverId: conversation_id }
                                            : session,
                                    ),
                                );
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
                        conversationId: conversationId ?? undefined,
                    },
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
        [activeSession?.id, activeSession?.serverId, patch, queryClient, scrollToBottom],
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
            const prefs = getVoicePreferences();
            const options = await voiceApi.options();
            // Electron cannot run Web Speech; fall forward to a server engine
            // instead of refusing to listen.
            const resolved = resolveSttChoice(options, prefs.stt);
            const sttMode = resolved.engine === "browser" ? "browser" : "server";
            // In browser mode the transcript is placed into the composer locally, so
            // the voice socket must not also run it as a turn.
            const engines = await voiceApi.engines();
            if (!engines.enabled) {
                setError("Voice is disabled.");
                return;
            }
            if (sttMode === "server") {
                // Check the *resolved* engine, not the deployment default: the
                // server degrades an unavailable pick silently, which would make
                // the Settings choice look like a gimmick.
                const check = checkSttChoice(options, resolved.engine);
                if (!check.ok) {
                    setError(check.reason ?? "Speech-to-text is unavailable.");
                    return;
                }
            }
            if (resolved.note) setError(resolved.note);
            const client = new VoiceClient(
                {
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
                },
                { stt: sttMode, autoTurn: false },
            );
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

    const panelClass = useMemo(
        () => cn("chat-panel", maximized ? "chat-panel-max" : "chat-panel-docked"),
        [maximized],
    );

    return (
        <aside className={panelClass} aria-label="Ultron chat" aria-hidden={!open} data-open={open}>
            <div className="chat-panel-backdrop" onClick={onClose} aria-hidden="true" />
            <ChatHistorySidebar
                open={open && historyOpen}
                onClose={() => setHistoryOpen(false)}
                sessions={historySummaries}
                activeId={activeSession?.id ?? null}
                onSelect={selectSession}
                onNew={startNewChat}
                onDelete={deleteSession}
            />
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
                        <button
                            type="button"
                            onClick={() => setHistoryOpen((v) => !v)}
                            aria-label="Chat history"
                            aria-pressed={historyOpen}
                            title="Chat history"
                        >
                            <History size={15} />
                        </button>
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
                        <button
                            type="button"
                            onClick={onClose}
                            aria-label="Close chat"
                            title="Close"
                        >
                            <X size={16} />
                        </button>
                    </div>
                </header>

                <div ref={scrollRef} onScroll={onScroll} className="chat-panel-transcript">
                    <div className="chat-transcript-inner">
                        {showSuggestions ? (
                            <div className="chat-empty animate-rise">
                                <h2 className="chat-empty-title">Good to see you.</h2>
                                <p className="chat-empty-sub">
                                    Chat with tools, memory, and voice. Try one of these:
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
                        busy={busy}
                        onStop={stopGeneration}
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
