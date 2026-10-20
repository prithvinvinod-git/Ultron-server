/**
 * Agent tool-timeline (ported from the Ultron reference repo's
 * `ui/ai-agent-response.tsx`, adapted to ULTRON design tokens). Renders the
 * live/settled trace: reasoning block, terminal command, web search, memory and
 * generic tool pills — each collapsible with its detail view.
 */
"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import {
  ChevronDown,
  Search,
  Terminal,
  Check,
  Brain,
  Command,
  Database,
  AlertCircle,
  CheckCheck,
  Copy,
} from "lucide-react";

/* ── Compact 3x3 pixel dot loader ────────────────────────────────────────── */
const CHEVRON_DELAYS = Array.from({ length: 9 }, (_, i) => {
  const r = Math.floor(i / 3);
  const c = i % 3;
  return (c + Math.abs(r - 1)) * 90;
});

export function PixelDotsLoader({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn("grid shrink-0 grid-cols-[repeat(3,3px)] items-center gap-[1.5px]", className)}
    >
      {CHEVRON_DELAYS.map((delay, index) => (
        <span
          key={index}
          className="size-[3px] rounded-full bg-foreground/80 transition-opacity motion-reduce:animate-none"
          style={{
            opacity: 0.2,
            animation: `agent-pixel-on 650ms cubic-bezier(0.23, 1, 0.32, 1) ${delay}ms infinite`,
          }}
        />
      ))}
    </span>
  );
}

/* ── Terminal command viewer ─────────────────────────────────────────────── */
export interface TerminalCommandProps {
  command: string;
  output?: string | undefined;
  exitCode?: number | undefined;
  isRunning?: boolean | undefined;
  className?: string | undefined;
}

export function TerminalCommand({
  command,
  output,
  exitCode = 0,
  isRunning = false,
  className,
}: TerminalCommandProps) {
  const [copied, setCopied] = React.useState(false);
  const handleCopy = () => {
    void navigator.clipboard.writeText(output ? `$ ${command}\n\n${output}` : `$ ${command}`);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  return (
    <div
      className={cn(
        "flex w-full flex-col overflow-hidden rounded-lg border border-border/80 bg-card font-mono text-[11.5px] select-text",
        className,
      )}
    >
      <div className="flex items-center justify-between border-b border-border/70 bg-muted/40 px-2.5 py-1.5 text-[11px]">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Terminal className="size-3.5 shrink-0 text-violet-500" aria-hidden="true" />
          <span className="select-none font-bold text-muted-foreground/60">$</span>
          <span className="truncate font-semibold tracking-tight text-foreground">{command}</span>
        </div>
        <div className="ml-2 flex shrink-0 items-center gap-2">
          {isRunning ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-violet-500/10 px-2 py-0.5 text-[10.5px] font-medium text-violet-500">
              <span className="size-1.5 animate-pulse rounded-full bg-violet-500" />
              running
            </span>
          ) : exitCode === 0 ? (
            <span className="inline-flex items-center rounded-md bg-emerald-500/10 px-1.5 py-0.5 text-[10.5px] font-medium tabular-nums text-emerald-500">
              exit 0
            </span>
          ) : (
            <span className="inline-flex items-center rounded-md bg-rose-500/10 px-1.5 py-0.5 text-[10.5px] font-medium tabular-nums text-rose-500">
              exit {exitCode}
            </span>
          )}
          <button
            type="button"
            onClick={handleCopy}
            aria-label="Copy command"
            className="flex items-center gap-1 rounded-sm px-1 py-0.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
          >
            {copied ? <CheckCheck className="size-3 text-emerald-500" /> : <Copy className="size-3" />}
          </button>
        </div>
      </div>
      {output ? (
        <div className="relative overflow-x-auto bg-muted/20 p-2.5 text-[11px] leading-relaxed text-muted-foreground">
          <pre className="whitespace-pre font-mono">
            {output.split("\n").map((line, idx) => {
              const isPass = line.includes("✓") || line.includes("PASS") || line.includes("passed");
              const isFail = line.includes("FAIL") || line.includes("Error") || line.includes("failed");
              const isWarn = line.includes("WARN") || line.includes("warning");
              return (
                <div
                  key={idx}
                  className={cn(
                    "flex items-start gap-1",
                    isPass && "font-medium text-emerald-500",
                    isFail && "font-medium text-rose-500",
                    isWarn && "text-amber-500",
                    !isPass && !isFail && !isWarn && "text-muted-foreground",
                  )}
                >
                  <span>{line}</span>
                </div>
              );
            })}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

/* ── Trace types ─────────────────────────────────────────────────────────── */
export type TraceNodeType = "reasoning" | "step" | "search" | "tool" | "terminal" | (string & {});

export type DetailLine = { text: string; tone?: "add" | "del" | "ctx" | "muted" | "error" | undefined };

export type TraceNode = {
  id?: string | undefined;
  type: TraceNodeType;
  toolName?: string | undefined;
  sentences?: string[] | undefined;
  durationSeconds?: number | undefined;
  primary?: string | undefined;
  secondary?: string | undefined;
  mono?: boolean | undefined;
  status?: "pending" | "running" | "completed" | "failed" | undefined;
  command?: string | undefined;
  output?: string | undefined;
  exitCode?: number | undefined;
  details?: DetailLine[] | undefined;
};

const SENT_H = 46;
const GAP = 8;
const MAX_H = 184;
const FADE = 18;

/* ── Nested reasoning block ──────────────────────────────────────────────── */
function NestedReasoningBlock({
  sentences,
  isActive,
  isFinished,
  durationSeconds = 4.2,
  onFinished,
}: {
  sentences: string[];
  isActive: boolean;
  isFinished: boolean;
  durationSeconds?: number | undefined;
  onFinished?: (() => void) | undefined;
}) {
  const [revealedCount, setRevealedCount] = React.useState(isFinished ? sentences.length : 0);
  const [manualOpen, setManualOpen] = React.useState(false);
  const [fade, setFade] = React.useState({ top: false, bottom: true });
  const viewportRef = React.useRef<HTMLDivElement>(null);
  const onFinishedRef = React.useRef(onFinished);
  React.useEffect(() => {
    onFinishedRef.current = onFinished;
  }, [onFinished]);

  React.useEffect(() => {
    if (!isActive || isFinished) return;
    const cadence = sentences.map(() => 1800 + Math.floor(Math.random() * 400));
    const totalMs = cadence.reduce((a, b) => a + b, 0);
    const timers: ReturnType<typeof setTimeout>[] = [];
    let cumulative = 0;
    cadence.forEach((delay, idx) => {
      cumulative += delay;
      timers.push(setTimeout(() => setRevealedCount(idx + 1), cumulative));
    });
    timers.push(setTimeout(() => onFinishedRef.current?.(), totalMs + 600));
    return () => timers.forEach(clearTimeout);
  }, [isActive, isFinished, sentences]);

  const expanded = isFinished ? manualOpen : isActive;
  const count = isFinished ? sentences.length : revealedCount;
  const contentH = count > 0 ? count * SENT_H + (count - 1) * GAP : 0;
  const capped = contentH > MAX_H;
  const viewH = capped ? MAX_H : contentH;
  const scrollable = isFinished && manualOpen;
  const translate = scrollable ? 0 : capped ? MAX_H - FADE - contentH : 0;
  const showTop = scrollable ? fade.top : capped;
  const showBottom = scrollable ? fade.bottom : capped;
  const mask = capped
    ? `linear-gradient(to bottom, transparent 0, #000 ${showTop ? FADE : 0}px, #000 calc(100% - ${
        showBottom ? FADE : 0
      }px), transparent 100%)`
    : "none";

  const onScroll = () => {
    const el = viewportRef.current;
    if (!el) return;
    setFade({ top: el.scrollTop > 1, bottom: el.scrollTop + el.clientHeight < el.scrollHeight - 1 });
  };

  return (
    <div className="flex w-full flex-col" style={{ animation: "agent-fade 280ms cubic-bezier(0.23,1,0.32,1) both" }}>
      <button
        type="button"
        disabled={!isFinished}
        aria-expanded={expanded}
        onClick={() => isFinished && setManualOpen((v) => !v)}
        className={cn(
          "group/row relative flex h-7 w-full items-center gap-2 rounded-md px-1.5 text-left text-[12px] transition-colors duration-150",
          isFinished ? "cursor-pointer hover:bg-muted/60 active:scale-[0.98]" : "cursor-default",
        )}
      >
        <span className="relative flex size-4 shrink-0 items-center justify-center text-muted-foreground">
          <Brain
            aria-hidden="true"
            className={cn(
              "size-3.5 opacity-75 transition-opacity duration-150",
              isFinished && "group-hover/row:opacity-0",
              manualOpen && "opacity-0",
            )}
          />
          {isFinished ? (
            <ChevronDown
              aria-hidden="true"
              className={cn(
                "absolute size-3.5 opacity-0 transition-transform duration-200 group-hover/row:opacity-100",
                manualOpen ? "rotate-0 opacity-100" : "-rotate-90",
              )}
            />
          ) : null}
        </span>
        <span className="text-[12px] font-medium">
          {isFinished ? (
            <span className="text-foreground">
              Thought for{" "}
              <span className="font-mono text-[11.5px] tabular-nums">{durationSeconds}s</span>
            </span>
          ) : (
            <span
              className="bg-clip-text font-medium text-transparent"
              style={{
                backgroundImage:
                  "linear-gradient(90deg, var(--muted-foreground) 35%, var(--foreground) 50%, var(--muted-foreground) 65%)",
                backgroundSize: "200% 100%",
                animation: "agent-shimmer 1.8s linear infinite",
              }}
            >
              Thinking…
            </span>
          )}
        </span>
      </button>

      <div
        className={cn(
          "grid transition-[grid-template-rows,opacity] duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
          expanded ? "grid-rows-[1fr] opacity-100" : "pointer-events-none grid-rows-[0fr] opacity-0",
        )}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="my-1 ml-2.5 border-l border-border/70 py-0.5 pl-2.5">
            <div
              ref={viewportRef}
              className={cn(
                "overflow-hidden pr-1 transition-[height] duration-300 ease-out",
                scrollable && "overflow-y-auto [&::-webkit-scrollbar]:hidden [scrollbar-width:none]",
              )}
              style={{ height: `${viewH}px`, WebkitMaskImage: mask, maskImage: mask }}
              onScroll={scrollable ? onScroll : undefined}
            >
              <div
                className="flex flex-col gap-2 transition-transform duration-500 ease-out will-change-transform"
                style={{ transform: `translateY(${translate}px)` }}
              >
                {sentences.slice(0, count).map((line, i) => (
                  <p
                    key={i}
                    className="m-0 line-clamp-2 h-[46px] overflow-hidden text-[13px] font-[425] leading-[23px] tracking-tight text-muted-foreground"
                    style={{ animation: "agent-fade 250ms cubic-bezier(0.23,1,0.32,1) both" }}
                  >
                    {line}
                  </p>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Trace pill row ──────────────────────────────────────────────────────── */
function TracePillRow({
  node,
  isActive,
}: {
  node: TraceNode;
  isActive: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const isCommandNode = Boolean(node.command || node.type === "terminal");
  const hasDetails = Boolean(
    isCommandNode || (node.details && node.details.length > 0) || node.output,
  );
  const primaryText = node.primary || (isCommandNode ? "Run" : undefined) || node.type;
  const secondaryText = node.secondary || node.command;
  const isMono = node.mono ?? isCommandNode;

  const renderIcon = () => {
    if (isActive || node.status === "running") {
      return (
        <span
          aria-hidden="true"
          className="size-3.5 shrink-0 animate-spin rounded-full border-[1.5px] border-muted-foreground/30 border-t-foreground"
        />
      );
    }
    if (node.status === "failed" || (node.exitCode !== undefined && node.exitCode > 0)) {
      return <AlertCircle className="size-3.5 shrink-0 text-rose-500" aria-hidden="true" />;
    }
    const key = `${node.primary || ""} ${node.toolName || ""} ${node.type} ${node.command || ""}`.toLowerCase();
    if (key.includes("run") || key.includes("exec") || isCommandNode) {
      return <Terminal className="size-3.5 shrink-0 text-violet-500" aria-hidden="true" />;
    }
    if (key.includes("search") || key.includes("query") || key.includes("lookup")) {
      return <Search className="size-3.5 shrink-0 text-blue-500" aria-hidden="true" />;
    }
    if (key.includes("memor") || key.includes("database") || key.includes("sql")) {
      return <Database className="size-3.5 shrink-0 text-emerald-500" aria-hidden="true" />;
    }
    if (node.type === "step") {
      return <Check className="size-3.5 shrink-0 text-emerald-500" aria-hidden="true" />;
    }
    return <Command className="size-3.5 shrink-0 text-muted-foreground/80" aria-hidden="true" />;
  };

  return (
    <div className="flex flex-col" style={{ animation: "agent-fade 280ms cubic-bezier(0.23,1,0.32,1) both" }}>
      <button
        type="button"
        disabled={!hasDetails}
        aria-expanded={open}
        onClick={() => hasDetails && setOpen((v) => !v)}
        className={cn(
          "group/row relative flex h-7 w-full items-center gap-2 rounded-md px-1.5 text-left text-[12px] transition-colors duration-150",
          hasDetails ? "cursor-pointer hover:bg-muted/60 active:scale-[0.98]" : "cursor-default",
        )}
      >
        <span className="relative flex size-4 shrink-0 items-center justify-center text-muted-foreground">
          <span className={cn("flex items-center justify-center transition-opacity duration-150", hasDetails && "group-hover/row:opacity-0", open && "opacity-0")}>
            {renderIcon()}
          </span>
          {hasDetails ? (
            <ChevronDown
              aria-hidden="true"
              className={cn(
                "absolute size-3.5 opacity-0 transition-transform duration-200 group-hover/row:opacity-100",
                open ? "rotate-0 opacity-100" : "-rotate-90",
              )}
            />
          ) : null}
        </span>
        <span className="shrink-0 text-[12px] font-medium tracking-tight text-foreground">{primaryText}</span>
        {secondaryText ? (
          <span
            className={cn(
              "inline-flex h-5 max-w-[65%] min-w-0 items-center truncate rounded-md border border-border/40 bg-muted/80 px-1.5 text-[11px] text-muted-foreground transition-colors group-hover/row:border-border/80 group-hover/row:text-foreground",
              isMono ? "font-mono" : "font-sans",
            )}
          >
            <span className="truncate">{secondaryText}</span>
          </span>
        ) : null}
      </button>

      {hasDetails ? (
        <div
          className={cn(
            "grid transition-[grid-template-rows,opacity] duration-300 ease-[cubic-bezier(0.23,1,0.32,1)]",
            open ? "grid-rows-[1fr] opacity-100" : "pointer-events-none grid-rows-[0fr] opacity-0",
          )}
        >
          <div className="min-h-0 overflow-hidden">
            <div className="my-1 ml-2.5 flex flex-col gap-1.5 border-l border-border/70 py-0.5 pl-2.5">
              {isCommandNode && node.command ? (
                <TerminalCommand command={node.command} output={node.output} exitCode={node.exitCode ?? 0} isRunning={isActive} />
              ) : null}
              {!isCommandNode && node.details && node.details.length > 0 ? (
                <div className="flex flex-col gap-1">
                  {node.details.map((line, lIdx) => (
                    <span
                      key={lIdx}
                      className={cn(
                        "text-[11.5px] leading-relaxed",
                        line.tone === "error" && "font-medium text-rose-500",
                        line.tone === "ctx" && "font-mono text-muted-foreground",
                        (!line.tone || line.tone === "muted") && "text-muted-foreground",
                      )}
                    >
                      {line.text}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* ── Thinking state (unboxed trigger + timeline) ─────────────────────────── */
export interface ThinkingStateProps extends React.HTMLAttributes<HTMLDivElement> {
  nodes?: TraceNode[] | undefined;
  live?: boolean | undefined;
  defaultExpanded?: boolean | undefined;
  workingLabel?: string | undefined;
}

export const ThinkingState = React.forwardRef<HTMLDivElement, ThinkingStateProps>(
  ({ nodes = [], live = false, defaultExpanded, workingLabel = "Working...", className, ...props }, ref) => {
    const [activeIndex] = React.useState(nodes.length);
    const [manualExpanded, setManualExpanded] = React.useState<boolean | null>(
      defaultExpanded !== undefined ? defaultExpanded : null,
    );
    const startTimeRef = React.useRef<number>(0);
    const [elapsedSeconds, setElapsedSeconds] = React.useState(1);

    React.useEffect(() => {
      if (!live) return;
      if (startTimeRef.current === 0) startTimeRef.current = Date.now();
      const timer = setInterval(() => {
        setElapsedSeconds(Math.max(1, Math.round((Date.now() - startTimeRef.current) / 1000)));
      }, 250);
      return () => clearInterval(timer);
    }, [live]);

    const expanded = manualExpanded !== null ? manualExpanded : live;

    return (
      <div ref={ref} className={cn("flex w-full select-none flex-col font-sans text-foreground", className)} {...props}>
        <style>{`
          @keyframes agent-pixel-on { 0%,100% { opacity:.15; transform:scale(.9);} 50% { opacity:.95; transform:scale(1.1);} }
          @keyframes agent-shimmer { 0%{background-position:200% 0;} 100%{background-position:-200% 0;} }
          @keyframes agent-fade { from{opacity:0;transform:translateY(2px);} to{opacity:1;transform:translateY(0);} }
        `}</style>
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setManualExpanded((prev) => !(prev !== null ? prev : live))}
          className="group flex w-fit items-center gap-1.5 bg-transparent p-0 text-left text-[13.5px] font-normal leading-relaxed text-muted-foreground/75 transition-colors duration-150 hover:text-foreground"
        >
          {live ? <PixelDotsLoader /> : null}
          <span className="text-[13.5px] font-normal">
            {live ? (
              <span
                className="bg-clip-text font-medium text-transparent"
                style={{
                  backgroundImage:
                    "linear-gradient(90deg, var(--muted-foreground) 35%, var(--foreground) 50%, var(--muted-foreground) 65%)",
                  backgroundSize: "200% 100%",
                  animation: "agent-shimmer 1.5s linear infinite",
                }}
              >
                {workingLabel}
              </span>
            ) : (
              <span>
                Worked for{" "}
                <span className="font-mono text-[12px] tabular-nums">{elapsedSeconds}</span>{" "}
                {elapsedSeconds === 1 ? "second" : "seconds"}
              </span>
            )}
          </span>
          <ChevronDown
            aria-hidden="true"
            className={cn("size-3 opacity-30 transition-transform duration-300 group-hover:opacity-80", expanded ? "rotate-180" : "rotate-0")}
          />
        </button>

        <div className={cn("grid transition-[grid-template-rows,opacity] duration-300 ease-out", expanded ? "grid-rows-[1fr] opacity-100" : "pointer-events-none grid-rows-[0fr] opacity-0")}>
          <div className="min-h-0 overflow-hidden">
            <div className="mt-1 ml-2 flex flex-col gap-0.5 border-l border-border/60 py-0.5 pl-2">
              {nodes.slice(0, activeIndex).map((node, idx) => {
                if (node.type === "reasoning" && node.sentences) {
                  return (
                    <NestedReasoningBlock
                      key={idx}
                      sentences={node.sentences}
                      durationSeconds={node.durationSeconds}
                      isActive={false}
                      isFinished
                    />
                  );
                }
                return <TracePillRow key={idx} node={node} isActive={false} />;
              })}
            </div>
          </div>
        </div>
      </div>
    );
  },
);
ThinkingState.displayName = "ThinkingState";
