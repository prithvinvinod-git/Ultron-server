/**
 * Composer (ported from the Ultron reference repo's `ui/ai-prompt-box.tsx`,
 * adapted to ULTRON tokens). A rounded prompt box with an autosizing textarea,
 * a slash-command tool menu backed by `/tools`, the mic capture button and the
 * send/stop action. Voice is controller-managed by the parent chat panel.
 */
"use client";

import * as React from "react";
import { ArrowUp, Square, AudioLines } from "lucide-react";

import { cn } from "@/lib/utils";
import { VoiceInput } from "./voice-input";

interface SlashTool {
  name: string;
  description?: string;
}

export interface ComposerProps {
  value: string;
  onValueChange: (value: string) => void;
  onSend: (text: string) => void;
  disabled?: boolean;
  listening?: boolean;
  voiceEnabled?: boolean;
  onBeginVoice?: () => void;
  onEndVoice?: () => void;
  placeholder?: string;
}

export function Composer({
  value,
  onValueChange,
  onSend,
  disabled = false,
  listening = false,
  voiceEnabled = true,
  onBeginVoice,
  onEndVoice,
  placeholder = "Message Ultron…",
}: ComposerProps) {
  const [recTime, setRecTime] = React.useState(0);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => {
    if (!listening) return;
    const id = window.setInterval(() => setRecTime((t) => t + 1), 1000);
    return () => {
      window.clearInterval(id);
      setRecTime(0);
    };
  }, [listening]);

  React.useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [value]);

  /* Slash commands — typing "/" offers the agent's tools. */
  const [tools, setTools] = React.useState<SlashTool[]>([]);
  const [dismissedQuery, setDismissedQuery] = React.useState<string | null>(null);
  const [slashIndex, setSlashIndex] = React.useState(0);
  const toolsLoadedRef = React.useRef(false);

  const slashQuery = React.useMemo(() => {
    if (!value.startsWith("/")) return null;
    if (value.includes("\n")) return null;
    const match = value.slice(1).match(/^([\w-]*)$/);
    return match ? (match[1] ?? null) : null;
  }, [value]);

  React.useEffect(() => {
    if (slashQuery === null || toolsLoadedRef.current) return;
    toolsLoadedRef.current = true;
    fetch("/tools")
      .then((r) => (r.ok ? r.json() : []))
      .then((data: unknown) => {
        const list = Array.isArray(data) ? data : (data as { tools?: SlashTool[] })?.tools;
        if (Array.isArray(list)) setTools(list as SlashTool[]);
      })
      .catch(() => {});
  }, [slashQuery]);

  const matches = React.useMemo(() => {
    if (slashQuery === null) return [];
    const q = slashQuery.toLowerCase();
    if (!q) return tools.slice(0, 8);
    return tools.filter((t) => t.name.toLowerCase().includes(q) || (t.description ?? "").toLowerCase().includes(q)).slice(0, 8);
  }, [tools, slashQuery]);

  const menuOpen = slashQuery !== null && matches.length > 0 && dismissedQuery !== slashQuery;

  const applySlashTool = (tool: SlashTool) => {
    onValueChange(`/${tool.name} `);
    setDismissedQuery(null);
    setSlashIndex(0);
    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  const hasContent = value.trim() !== "";

  const submit = () => {
    if (disabled || listening) return;
    if (value.trim()) onSend(value.trim());
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (menuOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIndex((i) => (i + 1) % matches.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIndex((i) => (i - 1 + matches.length) % matches.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const pick = matches[slashIndex] ?? matches[0];
        if (pick) applySlashTool(pick);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setDismissedQuery(slashQuery ?? null);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div
      className={cn(
        "chat-composer relative rounded-3xl border bg-card p-2 transition-all duration-300",
        listening ? "border-primary/70" : "border-border",
        disabled ? "border-primary/70" : "",
      )}
    >
      {listening ? (
        <div className="mb-1 inline-flex items-center gap-2 rounded-full border border-primary/40 bg-primary/10 px-2.5 py-1">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
          <span className="font-mono text-[11px] text-primary">
            {String(Math.floor(recTime / 60)).padStart(2, "0")}:{String(recTime % 60).padStart(2, "0")}
          </span>
          <span className="text-[11px] text-foreground/60">Recording…</span>
        </div>
      ) : null}

      {menuOpen ? (
        <div className="absolute bottom-full left-0 z-50 mb-2 w-full overflow-hidden rounded-2xl border border-border bg-card shadow-lg">
          <div className="border-b border-border px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Tools
          </div>
          <ul className="max-h-64 overflow-y-auto py-1">
            {matches.map((tool, i) => (
              <li key={tool.name}>
                <button
                  type="button"
                  onMouseEnter={() => setSlashIndex(i)}
                  onClick={() => applySlashTool(tool)}
                  className={cn(
                    "flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left transition-colors",
                    i === slashIndex ? "bg-primary/15" : "hover:bg-secondary",
                  )}
                >
                  <span className="font-mono text-sm text-foreground">/{tool.name}</span>
                  {tool.description ? (
                    <span className="line-clamp-2 text-xs text-muted-foreground">{tool.description}</span>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onValueChange(e.target.value)}
        onKeyDown={onKeyDown}
        rows={1}
        placeholder={placeholder}
        disabled={disabled}
        className="chat-composer-input min-h-[44px] w-full resize-none rounded-md bg-transparent px-3 py-2.5 text-base text-foreground placeholder:text-muted-foreground focus-visible:outline-none disabled:cursor-not-allowed"
      />

      <div className="flex items-center justify-between gap-2 pt-2">
        <div className="flex items-center gap-1" />

        <div className="flex items-center gap-1">
          {voiceEnabled ? (
            <VoiceInput
              listening={listening}
              elapsed={recTime}
              className="shrink-0"
              onPressStart={() => onBeginVoice?.()}
              onPressEnd={() => onEndVoice?.()}
            />
          ) : null}

          <button
            type="button"
            onClick={() => {
              if (listening) onEndVoice?.();
              else if (hasContent) submit();
              else onBeginVoice?.();
            }}
            disabled={disabled && !hasContent}
            aria-label={disabled ? "Stop generation" : listening ? "Stop recording" : hasContent ? "Send" : "Voice"}
            className={cn(
              "flex h-8 w-8 items-center justify-center rounded-full transition-all duration-200 disabled:pointer-events-none disabled:opacity-50",
              listening
                ? "bg-transparent text-rose-500 hover:bg-secondary"
                : hasContent
                  ? "bg-primary text-primary-foreground hover:opacity-90"
                  : "bg-transparent text-muted-foreground hover:bg-secondary hover:text-foreground",
            )}
          >
            {disabled ? (
              <Square className="h-4 w-4 animate-pulse" />
            ) : hasContent ? (
              <ArrowUp className="h-4 w-4" />
            ) : (
              <AudioLines className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
