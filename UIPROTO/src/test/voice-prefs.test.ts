import { describe, expect, it } from "vitest";

import type { VoiceOptions } from "@/lib/api";
import {
  browserSttSupported,
  checkSttChoice,
  checkTtsChoice,
  normalizeTags,
} from "@/lib/voice-prefs";

describe("normalizeTags", () => {
  it("trims, lower-cases and strips marker punctuation", () => {
    expect(normalizeTags([" Calm ", "[Excited]", "(whispering)"])).toEqual([
      "calm",
      "excited",
      "whispering",
    ]);
  });

  it("drops empty values and duplicates", () => {
    expect(normalizeTags(["calm", "", "   ", "CALM"])).toEqual(["calm"]);
  });

  it("ignores values that are not a list of strings", () => {
    expect(normalizeTags(undefined)).toEqual([]);
    expect(normalizeTags("calm")).toEqual([]);
    expect(normalizeTags([1, null, {}])).toEqual([]);
  });
});

const OPTIONS: VoiceOptions = {
  enabled: true,
  stt_engines: ["faster-whisper", "groq"],
  stt: [
    {
      engine: "groq",
      label: "Groq Whisper (cloud)",
      available: false,
      reason: "GROQ_API_KEY is not configured",
      sample_rate: 16000,
    },
    {
      engine: "faster-whisper",
      label: "Whisper (local)",
      available: true,
      reason: null,
      sample_rate: 16000,
    },
  ],
  tts: [
    {
      engine: "edge-tts",
      label: "Edge",
      available: true,
      default_voice: "en-US-AriaNeural",
      voices: [],
    },
    {
      engine: "fish-audio",
      label: "Fish Audio",
      available: false,
      reason: "insufficient credit",
      default_voice: "",
      voices: [],
    },
  ],
};

describe("checkSttChoice", () => {
  it("accepts a server engine the report says is available", () => {
    expect(checkSttChoice(OPTIONS, "faster-whisper")).toEqual({ ok: true });
  });

  it("rejects an unavailable engine with the server's reason", () => {
    expect(checkSttChoice(OPTIONS, "groq")).toEqual({
      ok: false,
      reason: "GROQ_API_KEY is not configured",
    });
  });

  it("rejects an engine this server does not offer", () => {
    const check = checkSttChoice({ ...OPTIONS, stt: [] }, "groq");
    expect(check.ok).toBe(false);
    expect(check.reason).toContain("does not offer");
  });

  it("does not block when an older server reports no per-engine list", () => {
    const legacy: VoiceOptions = {
      enabled: true,
      stt_engines: OPTIONS.stt_engines,
      tts: OPTIONS.tts,
    };
    expect(checkSttChoice(legacy, "groq")).toEqual({ ok: true });
  });

  it("rejects browser mode when the page has no Web Speech API", () => {
    const w = window as unknown as Record<string, unknown>;
    delete w['SpeechRecognition'];
    delete w['webkitSpeechRecognition'];
    expect(checkSttChoice(OPTIONS, "browser").ok).toBe(false);
  });
});

describe("browserSttSupported", () => {
  const w = window as unknown as Record<string, unknown>;

  function withApi(userAgent: string, run: () => void): void {
    const original = navigator.userAgent;
    Object.defineProperty(navigator, "userAgent", { configurable: true, value: userAgent });
    w['SpeechRecognition'] = class {};
    try {
      run();
    } finally {
      delete w['SpeechRecognition'];
      delete w['webkitSpeechRecognition'];
      Object.defineProperty(navigator, "userAgent", { configurable: true, value: original });
    }
  }

  it("accepts Chrome with the Web Speech API", () => {
    withApi("Mozilla/5.0 Chrome/130.0 Safari/537.36", () => {
      expect(browserSttSupported()).toBe(true);
    });
  });

  it("rejects Electron, which has the API but no speech credentials", () => {
    withApi("Mozilla/5.0 Chrome/130.0 Electron/44.9.0 Safari/537.36", () => {
      expect(browserSttSupported()).toBe(false);
    });
  });
});

describe("checkTtsChoice", () => {
  it("accepts an available provider", () => {
    expect(checkTtsChoice(OPTIONS, "edge-tts")).toEqual({ ok: true });
  });

  it("rejects an unavailable provider with its reason", () => {
    expect(checkTtsChoice(OPTIONS, "fish-audio")).toEqual({
      ok: false,
      reason: "insufficient credit",
    });
  });
});
