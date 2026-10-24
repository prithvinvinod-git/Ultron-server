/**
 * Client-side voice preferences.
 *
 * Engine choice is a *user* preference, not a deployment secret, so it lives in
 * the browser (localStorage) rather than the server env. The server still owns
 * which engines are actually *built*; these preferences pick between the ones it
 * reports, and the client degrades gracefully when a pick is unavailable.
 *
 * - STT: `groq` (server Whisper), `faster-whisper` (server, local model) or
 *   `browser` (Web Speech API, runs locally in the page).
 * - TTS: `fish-audio` (cloned voice), `edge-tts` (free neural voices) or
 *   `elevenlabs` (premium, needs a key).
 *
 * Fish Audio also reads *delivery cues* (`calm`, `excited`, `angry`, ...), which
 * it renders as inline markers in front of the text. Those are a taste
 * preference too, so they are stored here and sent with every turn; a provider
 * without cues simply ignores them.
 *
 * The chosen engine and voice travel with every voice turn, so switching
 * provider takes effect on the next sentence instead of the next restart.
 */
import { useSyncExternalStore } from "react";

import type { VoiceOptions } from "@/lib/api";

export type SttPreference = "groq" | "faster-whisper" | "browser";
export type TtsPreference = "edge-tts" | "elevenlabs" | "fish-audio";

/**
 * Delivery cues Fish Audio understands (S2 models). The picker offers these;
 * a free-text field lets the user type any of the 64+ expressions the provider
 * supports. They are sent as bare words and rendered as `[calm]` by the server.
 */
export const EMOTION_TAGS: readonly string[] = [
  "calm",
  "happy",
  "excited",
  "sad",
  "angry",
  "surprised",
  "curious",
  "confident",
  "serious",
  "friendly",
  "whispering",
  "laughing",
];

export interface VoicePreferences {
  /** Master switch — when off, the mic is never offered. */
  enabled: boolean;
  stt: SttPreference;
  tts: TtsPreference;
  /**
   * Preferred chat model id (`provider:model`), or empty for the deployment
   * default. Only a *preference*: the server falls back when the picked provider
   * is unconfigured, so a stale value never breaks a turn.
   */
  model: string;
  /** Preferred TTS voice id within the chosen TTS engine, or empty for default. */
  ttsVoice: string;
  /**
   * Delivery cues for the spoken reply (Fish Audio emotion tags). Empty means
   * "whatever the deployment defaults to".
   */
  ttsTags: string[];
}

export const DEFAULT_VOICE_PREFERENCES: VoicePreferences = {
  enabled: true,
  stt: "faster-whisper",
  tts: "edge-tts",
  model: "",
  ttsVoice: "",
  ttsTags: [],
};

const STORAGE_KEY = "ultron.voice.preferences";

/**
 * Bumped when a default changes in a way existing profiles should pick up.
 * Version 1 was stored without a version field; version 2 defaulted STT to the
 * browser engine. Both are migrated once to the current default (local Whisper),
 * which is the only engine that works on every surface, including Electron.
 */
const STORAGE_VERSION = 3;

const STT_PREFERENCES: readonly SttPreference[] = ["groq", "faster-whisper", "browser"];
const TTS_PREFERENCES: readonly TtsPreference[] = ["edge-tts", "elevenlabs", "fish-audio"];

function read(): VoicePreferences {
  if (typeof window === "undefined") return DEFAULT_VOICE_PREFERENCES;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_VOICE_PREFERENCES;
    const parsed = JSON.parse(raw) as Partial<VoicePreferences> & { version?: number };
    const version = parsed.version ?? 0;
    const outdated = version < STORAGE_VERSION;
    return {
      enabled: typeof parsed.enabled === "boolean" ? parsed.enabled : DEFAULT_VOICE_PREFERENCES.enabled,
      stt: outdated
        ? DEFAULT_VOICE_PREFERENCES.stt
        : STT_PREFERENCES.includes(parsed.stt as SttPreference)
          ? (parsed.stt as SttPreference)
          : DEFAULT_VOICE_PREFERENCES.stt,
      tts: TTS_PREFERENCES.includes(parsed.tts as TtsPreference)
        ? (parsed.tts as TtsPreference)
        : DEFAULT_VOICE_PREFERENCES.tts,
      model: typeof parsed.model === "string" ? parsed.model : "",
      ttsVoice: typeof parsed.ttsVoice === "string" ? parsed.ttsVoice : "",
      ttsTags: normalizeTags(parsed.ttsTags),
    };
  } catch {
    return DEFAULT_VOICE_PREFERENCES;
  }
}

/** Trimmed, de-duplicated, lower-case cues; anything unexpected is dropped. */
export function normalizeTags(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const cues: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string") continue;
    const cue = entry.trim().replace(/^[[(]+|[\])]+$/g, "").toLowerCase();
    if (cue && !seen.has(cue)) {
      seen.add(cue);
      cues.push(cue);
    }
  }
  return cues;
}

let current: VoicePreferences = read();
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

export function getVoicePreferences(): VoicePreferences {
  return current;
}

export function setVoicePreferences(patch: Partial<VoicePreferences>): VoicePreferences {
  current = { ...current, ...patch };
  if (patch.ttsTags !== undefined) {
    current = { ...current, ttsTags: normalizeTags(patch.ttsTags) };
  }
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...current, version: STORAGE_VERSION }));
    } catch {
      /* storage disabled (private mode) — keep the in-memory value */
    }
  }
  emit();
  return current;
}

export function resetVoicePreferences(): VoicePreferences {
  current = { ...DEFAULT_VOICE_PREFERENCES };
  if (typeof window !== "undefined") {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }
  emit();
  return current;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Subscribe a component to the stored preferences (SSR-safe default). */
export function useVoicePreferences(): VoicePreferences {
  return useSyncExternalStore(subscribe, getVoicePreferences, () => DEFAULT_VOICE_PREFERENCES);
}

/**
 * Whether this window can transcribe speech locally.
 *
 * Electron ships Chromium *without* Google's speech credentials, so
 * `webkitSpeechRecognition` exists there but every session dies with a
 * `network` error. Reporting it as unsupported keeps the picker honest instead
 * of leaving the user with a microphone that silently hears nothing.
 */
export function browserSttSupported(): boolean {
  if (typeof window === "undefined") return false;
  if (window.navigator.userAgent.includes("Electron")) return false;
  const w = window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown };
  return Boolean(w.SpeechRecognition ?? w.webkitSpeechRecognition);
}

/** The outcome of checking a preference against the server's report. */
export interface EngineCheck {
  ok: boolean;
  reason?: string;
}

interface ProviderAvailability {
  engine: string;
  available: boolean;
  reason?: string | null;
}

function providerCheck(
  providers: readonly ProviderAvailability[] | undefined,
  engine: string,
  kind: string,
): EngineCheck {
  // An older server may not report per-engine availability; do not block on
  // what cannot be verified.
  if (!providers) return { ok: true };
  const provider = providers.find((entry) => entry.engine === engine);
  if (!provider) {
    return { ok: false, reason: `This server does not offer the ${kind} engine "${engine}".` };
  }
  if (!provider.available) {
    return {
      ok: false,
      reason: provider.reason ?? `The ${kind} engine "${engine}" is unavailable on this server.`,
    };
  }
  return { ok: true };
}

/**
 * Verify the stored STT choice against the server's honest availability report.
 *
 * The server degrades a request for an unavailable engine to its configured one,
 * so without this check the picker would silently do nothing. The UI instead
 * refuses to start with a reason, and Settings shows which engines can run.
 */
export function checkSttChoice(options: VoiceOptions, engine: SttPreference): EngineCheck {
  if (engine === "browser") {
    return browserSttSupported()
      ? { ok: true }
      : {
          ok: false,
          reason:
            "This browser cannot transcribe locally. Pick a server speech-to-text engine in Settings.",
        };
  }
  return providerCheck(options.stt, engine, "speech-to-text");
}

/** Verify the stored TTS choice the same way (availability, or a reason). */
export function checkTtsChoice(options: VoiceOptions, engine: TtsPreference): EngineCheck {
  return providerCheck(options.tts, engine, "text-to-speech");
}

/** Server engines tried, in order, when local speech recognition cannot run. */
const SERVER_STT_ORDER: SttPreference[] = ["faster-whisper", "groq"];

/**
 * The engine that will actually be used, starting from the stored preference.
 *
 * `browser` is the default because it needs no server model, but Electron (and
 * any browser without Web Speech) cannot run it. Rather than refusing to start
 * there, fall forward to the best available server engine and report why, so a
 * fresh install hears the microphone instead of showing an error.
 */
export function resolveSttChoice(
  options: VoiceOptions,
  engine: SttPreference,
): { engine: SttPreference; note?: string } {
  if (checkSttChoice(options, engine).ok) return { engine };
  if (engine === "browser") {
    for (const candidate of SERVER_STT_ORDER) {
      if (checkSttChoice(options, candidate).ok) {
        return {
          engine: candidate,
          note: `Browser speech is unavailable here; using ${candidate} instead.`,
        };
      }
    }
  }
  return { engine };
}
