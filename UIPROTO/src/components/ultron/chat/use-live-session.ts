/**
 * Hands-free live session for ULTRON.
 *
 * The reference repo's `useLiveSession` is a *client-side* conversational loop
 * (browser Web Speech + local TTS). ULTRON differs: the server owns STT, TTS,
 * barge-in and the turn itself over `WS /voice/ws` (see `lib/voice.ts`). So this
 * hook does not re-implement that loop — it *adapts* ULTRON's `VoiceClient` onto
 * the same `LiveMode` view contract (status, subtitle, captions, level).
 *
 * Cursor contract:
 *  - user speech  → `stt_partial` (subtitle draft) → `stt_final` (subtitle hold)
 *  - ULTRON reply → `answer` (a caption line)
 *  - turn phase   → `state` (`listening` / `thinking` / `speaking`)
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { VoiceClient } from "@/lib/voice";
import type { VoiceState } from "@/lib/voice";

import type { ActivityKind } from "./activity";
import type { LiveCaption, LiveStatus } from "./live-mode";

export interface UseLiveSessionOptions {
  onError?: (message: string) => void;
  /** Optional hook to mirror what the agent is doing from a chat turn. */
  activity?: ActivityKind | null;
}

export function useLiveSession({ onError, activity = null }: UseLiveSessionOptions = {}) {
  const [active, setActive] = useState(false);
  const [status, setStatus] = useState<LiveStatus>("idle");
  const [subtitle, setSubtitle] = useState("");
  const [captions, setCaptions] = useState<LiveCaption[]>([]);
  const [level, setLevel] = useState(0);

  const clientRef = useRef<VoiceClient | null>(null);
  const activeRef = useRef(false);
  const captionSeq = useRef(0);
  /** The last user utterance, held as the subtitle until the next turn. */
  const heardRef = useRef("");

  const setStatusSafe = useCallback((next: LiveStatus) => setStatus(next), []);

  const start = useCallback(async () => {
    if (activeRef.current) return;
    activeRef.current = true;
    setActive(true);
    setStatusSafe("listening");
    setSubtitle("");
    setCaptions([]);
    heardRef.current = "";
    captionSeq.current = 0;

    const client = new VoiceClient({
      onState: (state: VoiceState) => {
        if (state === "closed") {
          activeRef.current = false;
          setActive(false);
          setStatusSafe("idle");
          setLevel(0);
          clientRef.current = null;
          return;
        }
        if (state === "error") {
          setStatusSafe("error");
          return;
        }
        if (state === "listening" || state === "thinking" || state === "speaking") {
          setStatusSafe(state);
        }
      },
      onPartial: (text) => {
        heardRef.current = text;
        setSubtitle(text);
      },
      onFinal: (text) => {
        heardRef.current = text;
        setSubtitle(text);
      },
      onAnswer: (text) => {
        if (!text.trim()) return;
        captionSeq.current += 1;
        setCaptions((current) => [...current, { id: `cap-${captionSeq.current}`, text }]);
      },
      onLevel: (value) => setLevel(value),
      onError: (message) => {
        setStatusSafe("error");
        onError?.(message);
      },
    });

    clientRef.current = client;
    try {
      // Unlock the playback graph from the user gesture, then connect.
      client.unlock();
      await client.start();
    } catch (err) {
      activeRef.current = false;
      setActive(false);
      setStatusSafe("idle");
      clientRef.current = null;
      onError?.(err instanceof Error ? err.message : "Could not start live mode.");
    }
  }, [onError, setStatusSafe]);

  const stop = useCallback(() => {
    activeRef.current = false;
    clientRef.current?.stop();
    clientRef.current = null;
    setActive(false);
    setStatusSafe("idle");
    setSubtitle("");
    setLevel(0);
  }, [setStatusSafe]);

  useEffect(
    () => () => {
      activeRef.current = false;
      clientRef.current?.stop();
      clientRef.current = null;
    },
    [],
  );

  return {
    active,
    status,
    subtitle,
    captions,
    level,
    activity,
    start,
    stop,
  };
}
