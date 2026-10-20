/**
 * Message bubble (ported from the Ultron reference repo, adapted to ULTRON
 * tokens and the `/chat/stream` event set). Renders the user turn, the
 * streaming assistant turn (thinking orb, reasoning block, tool timeline,
 * markdown) and the done meta row.
 */
"use client";

import { BrainCog, Square, TriangleAlert, Volume2 } from "lucide-react";
import { Markdown } from "./markdown";
import { cn } from "@/lib/utils";
import { ThinkingState, type TraceNode } from "./agent-response";
import { ThinkingOrb } from "./thinking-orbs";
import type { ChatMessage } from "./types";

export function MessageBubble({
  message,
  onSpeak,
  isSpeaking,
}: {
  message: ChatMessage;
  onSpeak?: (id: string, text: string) => void;
  isSpeaking?: boolean;
}) {
  const isUser = message.role === "user";
  const hasThinking = !!message.thinking;
  const hasTools = (message.toolSteps?.length ?? 0) > 0;
  const timeline = buildTimeline(message);
  const showOrbBox = message.streaming && hasThinking && !hasTools;
  const showTimeline = !!timeline && (!message.streaming || hasTools);

  return (
    <div className="chat-message animate-rise flex w-full gap-3 text-[13px]">
      <div className={cn("flex min-w-0 max-w-full flex-col gap-1", isUser && "ml-auto")}>
        {message.error ? (
          <div className="flex items-center gap-2 rounded-xl border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-400">
            <TriangleAlert size={15} />
            <span className="min-w-0">{message.error}</span>
          </div>
        ) : null}

        {isUser ? (
          <div className="ml-auto max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 text-[13.5px] leading-relaxed text-foreground">
            {message.text}
          </div>
        ) : (
          <>
            {showOrbBox ? (
              <div className="max-w-[85%] rounded-xl bg-card/80 px-3 py-2 text-xs text-muted-foreground">
                <span className="mb-1 flex items-center gap-2 font-medium text-foreground/70">
                  <ThinkingOrb state="composing" size={20} theme="dark" />
                  Thinking
                </span>
                <div className="max-h-28 overflow-y-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed opacity-80">
                  {message.thinking}
                </div>
              </div>
            ) : null}

            {showTimeline && timeline ? (
              <div className="max-w-[85%]">
                <ThinkingState nodes={timeline} live={message.streaming} defaultExpanded={message.streaming} workingLabel="Working..." />
              </div>
            ) : null}

            <div className="max-w-[85%]">
              {message.text ? (
                <Markdown>{message.text}</Markdown>
              ) : message.streaming ? (
                !showOrbBox && !showTimeline ? (
                  <span className="flex items-center gap-2 py-1 text-sm text-muted-foreground">
                    <ThinkingOrb state="working" size={20} theme="dark" />
                    Working
                  </span>
                ) : null
              ) : (
                <span className="text-sm text-muted-foreground">No response.</span>
              )}
            </div>

            {(message.provider || message.streaming) && (
              <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                <span>
                  {message.provider?.toUpperCase() ?? ""}
                  {message.model ? ` · ${message.model}` : ""}
                </span>
                {!message.streaming && message.text && onSpeak ? (
                  <button
                    type="button"
                    onClick={() => onSpeak(message.id, message.text)}
                    className={cn(
                      "flex items-center gap-1 transition",
                      isSpeaking ? "text-primary" : "text-muted-foreground hover:text-primary",
                    )}
                    aria-label={isSpeaking ? "Stop speaking" : "Speak response"}
                  >
                    {isSpeaking ? <Square size={12} /> : <Volume2 size={12} />}
                    <span>{isSpeaking ? "Stop" : "Listen"}</span>
                  </button>
                ) : null}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function buildTimeline(message: ChatMessage): TraceNode[] | null {
  const nodes: TraceNode[] = [];

  if (message.thinking) {
    nodes.push({
      type: "reasoning",
      sentences: splitSentences(message.thinking, 6),
      durationSeconds: Math.max(1, Math.round(message.thinking.length / 90)),
    });
  }

  for (const step of message.toolSteps ?? []) {
    const { state, name, result, error } = step;
    const isSearch = name === "search_web" || /search|web|lookup|query/.test(name);
    const isMemory = name === "recall_memories" || name === "store_memory" || /memor/.test(name);
    const looksLikeCommand =
      name === "execute_command" || /(^|\s)((npm|yarn|npx|pnpm|tsc|node|git|kubectl|python|curl)\s)/.test(name);

    if (state === "error") {
      nodes.push({
        type: "tool",
        toolName: name,
        status: "failed",
        primary: name.replace(/_/g, " "),
        secondary: truncate(error ?? "failed", 60),
        details: error ? [{ text: error, tone: "error" }] : undefined,
      });
      continue;
    }

    if (looksLikeCommand) {
      nodes.push({
        type: "terminal",
        status: state === "running" ? "running" : "completed",
        command: name.replace(/^execute_command\s*/, ""),
        exitCode: 0,
        output: result,
      });
      continue;
    }

    if (isSearch) {
      nodes.push({
        type: "search",
        toolName: "search_web",
        status: "completed",
        primary: "Web search",
        secondary: state === "running" ? "Searching the web…" : result ? truncate(result, 64) : "Search completed",
        details:
          state === "done" && result
            ? result.split("\n").filter((l) => l.trim()).slice(0, 6).map((l) => ({ text: truncate(l, 160), tone: "ctx" as const }))
            : undefined,
      });
      continue;
    }

    if (isMemory) {
      nodes.push({
        type: "tool",
        toolName: name,
        status: state === "running" ? "running" : "completed",
        primary: name === "recall_memories" ? "Recalling memories" : "Saving memory",
        secondary: state === "running" ? "Working…" : result ? truncate(result, 60) : "Done",
        details:
          state === "done" && result
            ? result.split("\n").filter((l) => l.trim()).slice(0, 5).map((l) => ({ text: truncate(l, 140), tone: "ctx" as const }))
            : undefined,
      });
      continue;
    }

    nodes.push({
      type: "tool",
      toolName: name,
      status: state === "running" ? "running" : "completed",
      primary: name.replace(/_/g, " "),
      secondary: state === "running" ? "running…" : result ? truncate(result, 60) : "completed",
      details:
        state === "done" && result
          ? result.split("\n").filter((l) => l.trim()).slice(0, 8).map((l) => ({ text: truncate(l, 140), tone: "ctx" as const }))
          : undefined,
    });
  }

  return nodes.length > 0 ? nodes : null;
}

function splitSentences(text: string, max: number): string[] {
  const lines = text
    .split(/\n+|(?<=[.!?])\s+/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
  return lines.slice(0, max);
}

function truncate(text: string, max: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max)}…` : clean;
}
