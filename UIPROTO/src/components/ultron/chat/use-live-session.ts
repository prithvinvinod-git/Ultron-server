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

import { VoiceClient, speakText } from "@/lib/voice";
import type { VoiceState } from "@/lib/voice";
import { voiceApi } from "@/lib/api";
import { checkSttChoice, checkTtsChoice, resolveSttChoice, useVoicePreferences } from "@/lib/voice-prefs";

import type { ActivityKind } from "./activity";

export type LiveStatus = "idle" | "listening" | "thinking" | "speaking" | "error";
export interface LiveCaption {
  id: string;
  text: string;
}

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
  const prefs = useVoicePreferences();

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

    let options: Awaited<ReturnType<typeof voiceApi.options>>;
    try {
      options = await voiceApi.options();
    } catch (err) {
      activeRef.current = false;
      setActive(false);
      setStatusSafe("idle");
      onError?.(err instanceof Error ? err.message : "Could not reach the voice service.");
      return;
    }
    // Electron cannot run Web Speech; fall forward to a server engine rather
    // than failing to start.
    const resolved = resolveSttChoice(options, prefs.stt);

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
    }, {
      stt: resolved.engine === "browser" ? "browser" : "server",
      sttEngine: resolved.engine === "browser" ? "" : resolved.engine,
      ttsEngine: prefs.tts,
      ttsVoice: prefs.ttsVoice,
      ttsTags: prefs.ttsTags,
    });

    clientRef.current = client;
    try {
      // Refuse to start on a pick the server cannot run: it would silently
      // substitute the deployment default, which is exactly the "UI gimmick"
      // the Settings pickers must not be.
      if (!options.enabled) throw new Error("Voice is disabled on the server.");
      const sttCheck = checkSttChoice(options, resolved.engine);
      if (!sttCheck.ok) throw new Error(sttCheck.reason ?? "Speech-to-text is unavailable.");
      const ttsCheck = checkTtsChoice(options, prefs.tts);
      if (!ttsCheck.ok) throw new Error(ttsCheck.reason ?? "Text-to-speech is unavailable.");
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
  }, [onError, setStatusSafe, prefs]);

  const stop = useCallback(() => {
    activeRef.current = false;
    clientRef.current?.stop();
    clientRef.current = null;
    setActive(false);
    setStatusSafe("idle");
    setSubtitle("");
    setLevel(0);
  }, [setStatusSafe]);

  const sendText = useCallback((text: string) => {
    clientRef.current?.sendText(text, prefs.ttsVoice || undefined);
  }, [prefs.ttsVoice]);

  /**
   * Speak a one-off line (the entry greeting) directly through TTS and echo it
   * as a caption. Not an agent turn: it must be instant and must not depend on
   * the model, matching the reference client's `speak()`.
   */
  const greet = useCallback(async (text: string) => {
    const line = text.trim();
    if (!line) return;
    captionSeq.current += 1;
    setCaptions((current) => [...current, { id: `cap-${captionSeq.current}`, text: line }]);
    await speakText(line, prefs.ttsVoice || undefined, prefs.tts, prefs.ttsTags);
  }, [prefs.tts, prefs.ttsTags, prefs.ttsVoice]);

  // A provider picked in Settings reaches a live session through a config frame,
  // so switching takes effect on the next turn instead of the next restart.
  useEffect(() => {
    clientRef.current?.setEngines({
      sttEngine: prefs.stt === "browser" ? "" : prefs.stt,
      ttsEngine: prefs.tts,
      ttsVoice: prefs.ttsVoice,
      ttsTags: prefs.ttsTags,
    });
  }, [prefs.stt, prefs.tts, prefs.ttsTags, prefs.ttsVoice]);

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
    sendText,
    greet,
  };
}
