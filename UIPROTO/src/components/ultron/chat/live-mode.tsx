/**
 * Hands-free live view (ported from the Ultron reference repo's
 * `voice/live-mode.tsx`, adapted to ULTRON). Renders the voice-sensitive orb,
 * the current status line + live user subtitle, the End button and an
 * auto-scrolling teleprompter of the assistant's captions and tool activity.
 *
 * ULTRON's own `VoiceClient` owns the mic and the server owns STT/TTS, so the
 * orb is driven by the `level` the session reports.
 */
"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";
import { activityLabel, type ActivityKind } from "./activity";
import { VoicePoweredOrb } from "./voice-powered-orb";

export type LiveStatus = "idle" | "listening" | "thinking" | "speaking" | "error";

export interface LiveCaption {
  id: string;
  text: string;
}

export function LiveMode({
  status,
  activity,
  subtitle,
  captions,
  level = 0,
  onStop,
}: {
  status: LiveStatus;
  activity?: ActivityKind | null;
  subtitle?: string;
  captions: LiveCaption[];
  level?: number;
  onStop: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [captions, status]);

  const speaking = status === "speaking";
  const statusLine =
    status === "listening"
      ? "Listening…"
      : status === "speaking"
        ? "Speaking…"
        : activity
          ? activityLabel(activity)
          : "Thinking…";

  return (
    <div className="live-mode">
      <div className={cn("live-orb", status === "listening" && "is-listening")}>
        <VoicePoweredOrb level={level} className="overflow-visible" />
        {speaking ? (
          <div className="orb-wave-bars">
            {[0, 1, 2, 3, 4].map((i) => (
              <span key={i} className="orb-wave" style={{ animationDelay: `${i * 0.12}s` }} />
            ))}
          </div>
        ) : null}
      </div>

      <div className="live-status">
        <p className="live-status-line">{statusLine}</p>
        {subtitle ? (
          <p className="live-subtitle">“{subtitle}”</p>
        ) : (
          <p className="live-hint">Say “stop” to cut in, or just talk over me.</p>
        )}
        <button type="button" onClick={onStop} className="live-end">
          End live session
        </button>
      </div>

      <div ref={scrollRef} className="live-captions">
        <div className="live-captions-inner">
          {captions.length === 0 ? (
            <p className="live-captions-empty">
              {speaking ? "…" : "Ask me anything — I'll answer here in large print."}
            </p>
          ) : (
            captions.map((c, i) => (
              <p
                key={c.id}
                className={cn(
                  "live-caption",
                  i === captions.length - 1 ? "live-caption-last" : "live-caption-prev",
                )}
              >
                {c.text}
              </p>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
