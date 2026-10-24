/**
 * Settings surface — voice and model configuration.
 *
 * Engine choice is a per-user preference (kept in localStorage, see
 * `lib/voice-prefs`), so this panel reads the server's honest availability
 * report (`GET /voice/engines`, `GET /voice/options`, `GET /models`) and lets the
 * operator pick from what the deployment can actually run, rather than pretending
 * to configure the server. A pick the server cannot build is shown as unavailable
 * with its reason, so the choice is never silently ignored (§64.11).
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Mic, Volume2, Settings as SettingsIcon, RotateCcw, Cpu, Wand2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { modelsApi, voiceApi } from "@/lib/api";
import {
  EMOTION_TAGS,
  browserSttSupported,
  resetVoicePreferences,
  setVoicePreferences,
  useVoicePreferences,
} from "@/lib/voice-prefs";
import type { SttPreference, TtsPreference } from "@/lib/voice-prefs";

interface Choice<T extends string> {
  value: T;
  label: string;
  note: string;
}

const STT_CHOICES: Array<Choice<SttPreference>> = [
  { value: "faster-whisper", label: "Whisper (local)", note: "Runs on the server, no key, small model." },
  { value: "groq", label: "Groq Whisper", note: "Cloud transcription, needs GROQ_API_KEY." },
  { value: "browser", label: "Browser (Web Speech)", note: "Runs on this device; no key, nothing uploaded for recognition." },
];

const TTS_CHOICES: Array<Choice<TtsPreference>> = [
  { value: "fish-audio", label: "Fish Audio", note: "Your cloned voice; the key stays on the server." },
  { value: "edge-tts", label: "Edge TTS", note: "Free Microsoft neural voices." },
  { value: "elevenlabs", label: "ElevenLabs", note: "Premium voices, needs ELEVENLABS_API_KEY." },
];

function ChoiceRow<T extends string>({
  id,
  choices,
  value,
  onSelect,
  disabled,
}: {
  id: string;
  choices: Array<Choice<T>>;
  value: T;
  onSelect: (next: T) => void;
  disabled?: (choice: T) => string | null;
}) {
  return (
    <div className="settings-choices" role="radiogroup" aria-label={id}>
      {choices.map((choice) => {
        const reason = disabled?.(choice.value) ?? null;
        const selected = value === choice.value;
        return (
          <button
            key={choice.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={reason !== null}
            title={reason ?? choice.note}
            onClick={() => onSelect(choice.value)}
            className={`settings-choice${selected ? " is-active" : ""}${reason ? " is-disabled" : ""}`}
          >
            <span className="settings-choice-label">{choice.label}</span>
            <span className="settings-choice-note">{reason ?? choice.note}</span>
          </button>
        );
      })}
    </div>
  );
}

export function SettingsPanel() {
  const prefs = useVoicePreferences();
  const engines = useQuery({ queryKey: ["voice", "engines"], queryFn: voiceApi.engines, retry: false });
  const modelCatalog = useQuery({ queryKey: ["models", "catalog"], queryFn: modelsApi.catalog, retry: false });
  const voiceOptions = useQuery({ queryKey: ["voice", "options"], queryFn: voiceApi.options, retry: false });
  const [clonedVoice, setClonedVoice] = useState("");
  const [customTag, setCustomTag] = useState("");

  const browserOk = browserSttSupported();
  const sttReason = (choice: SttPreference): string | null => {
    if (choice === "browser") return browserOk ? null : "This browser has no speech recognition.";
    const providers = voiceOptions.data?.stt;
    if (providers) {
      const provider = providers.find((entry) => entry.engine === choice);
      if (!provider) return "The server does not offer this engine.";
      return provider.available ? null : provider.reason ?? "Not configured on the server.";
    }
    if (!engines.data) return null;
    const active = engines.data.stt.engine.split(">")[0]?.trim() ?? engines.data.stt.engine;
    return active === choice && engines.data.stt.available ? null : engines.data.stt.reason ?? "Not configured on the server.";
  };
  const ttsReason = (choice: TtsPreference): string | null => {
    const provider = voiceOptions.data?.tts.find((entry) => entry.engine === choice);
    if (provider) return provider.available ? null : provider.reason ?? "Not configured on the server.";
    if (!engines.data) return null;
    const active = engines.data.tts.engine.split(">")[0]?.trim() ?? engines.data.tts.engine;
    return active === choice && engines.data.tts.available ? null : engines.data.tts.reason ?? "Not configured on the server.";
  };

  const activeTts = voiceOptions.data?.tts.find((provider) => provider.engine === prefs.tts)
    ?? voiceOptions.data?.tts.find((provider) => provider.engine === "edge-tts")
    ?? voiceOptions.data?.tts[0];
  const ttsVoiceChoices = activeTts?.voices ?? [];
  const fishAudio = voiceOptions.data?.tts.find((provider) => provider.engine === "fish-audio");
  const modelChoices = (modelCatalog.data?.models ?? []).map((model) => ({
    ...model,
    local: modelCatalog.data?.providers.find((provider) => provider.name === model.provider)?.local ?? false,
  }));

  const insertClonedVoice = () => {
    const voice = clonedVoice.trim();
    if (!voice) return;
    setVoicePreferences({ tts: "fish-audio", ttsVoice: voice });
    setClonedVoice("");
  };

  const toggleTag = (tag: string) => {
    const active = prefs.ttsTags.includes(tag);
    setVoicePreferences({
      ttsTags: active
        ? prefs.ttsTags.filter((cue) => cue !== tag)
        : [...prefs.ttsTags, tag],
    });
  };

  const addCustomTag = () => {
    const cue = customTag.trim();
    if (!cue) return;
    setVoicePreferences({ ttsTags: [...prefs.ttsTags, cue] });
    setCustomTag("");
  };

  return (
    <div className="shell-panels">
      <section className="hud-panel">
        <div className="panel-heading">
          <h2>VOICE SETTINGS</h2>
          <SettingsIcon size={13} />
        </div>

        <div className="settings-group">
          <label className="settings-toggle">
            <input
              type="checkbox"
              checked={prefs.enabled}
              onChange={(event) => setVoicePreferences({ enabled: event.target.checked })}
            />
            <span>Enable voice</span>
            <span className="settings-toggle-note">
              Turn the microphone and spoken replies on or off everywhere.
            </span>
          </label>
        </div>

        <div className="settings-group">
          <span className="settings-group-title">Speech recognition (STT)</span>
          <ChoiceRow
            id="stt-choice"
            choices={STT_CHOICES}
            value={prefs.stt}
            onSelect={(next) => setVoicePreferences({ stt: next })}
            disabled={sttReason}
          />
        </div>

        <div className="settings-group">
          <span className="settings-group-title">Voice output (TTS)</span>
          <ChoiceRow
            id="tts-choice"
            choices={TTS_CHOICES}
            value={prefs.tts}
            onSelect={(next) => setVoicePreferences({ tts: next, ttsVoice: "" })}
            disabled={ttsReason}
          />
        </div>

        <div className="settings-group">
          <span className="settings-group-title">
            <Wand2 size={11} /> Cloned voice (Fish Audio)
          </span>
          <div className="settings-field">
            <input
              type="text"
              value={clonedVoice}
              placeholder={fishAudio?.default_voice || "reference id of the cloned voice"}
              aria-label="Fish Audio cloned voice id"
              onChange={(event) => setClonedVoice(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") insertClonedVoice();
              }}
            />
            <Button variant="ghost" onClick={insertClonedVoice} disabled={!clonedVoice.trim()}>
              INSERT
            </Button>
          </div>
          <span className="settings-choice-note">
            {fishAudio
              ? fishAudio.available
                ? "The API key stays on the server. Insert the id of a clone to speak with that voice."
                : fishAudio.reason ?? "Fish Audio is not configured on the server."
              : "Fish Audio is not configured on the server (FISH_AUDIO_API_KEY and FISH_AUDIO_VOICE_ID)."}
          </span>
        </div>

        <div className="settings-group">
          <span className="settings-group-title">Voice (TTS model)</span>
          {ttsVoiceChoices.length ? (
            <select
              className="settings-select"
              aria-label="tts-voice-choice"
              value={prefs.ttsVoice || activeTts?.default_voice || ""}
              onChange={(event) => setVoicePreferences({ ttsVoice: event.target.value })}
            >
              {ttsVoiceChoices.map((voice) => (
                <option key={voice.id} value={voice.id}>
                  {voice.label}
                </option>
              ))}
            </select>
          ) : (
            <span className="settings-choice-note">
              {voiceOptions.isLoading
                ? "Loading voices…"
                : "No voices available for the selected TTS provider."}
            </span>
          )}
        </div>

        <div className="settings-group">
          <span className="settings-group-title">
            <Wand2 size={11} /> Emotion cues (Fish Audio)
          </span>
          <div className="settings-tag-grid" role="group" aria-label="emotion-cues">
            {EMOTION_TAGS.map((tag) => {
              const selected = prefs.ttsTags.includes(tag);
              return (
                <button
                  key={tag}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => toggleTag(tag)}
                  className={`settings-tag${selected ? " is-active" : ""}`}
                >
                  {tag}
                </button>
              );
            })}
          </div>
          <div className="settings-field">
            <input
              type="text"
              value={customTag}
              placeholder="another expression, e.g. whispering"
              aria-label="Fish Audio emotion cue"
              onChange={(event) => setCustomTag(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") addCustomTag();
              }}
            />
            <Button variant="ghost" onClick={addCustomTag} disabled={!customTag.trim()}>
              ADD
            </Button>
          </div>
          {prefs.ttsTags.length ? (
            <div className="settings-field">
              <span className="settings-choice-note">Active: {prefs.ttsTags.join(", ")}</span>
              <Button variant="ghost" onClick={() => setVoicePreferences({ ttsTags: [] })}>
                CLEAR
              </Button>
            </div>
          ) : null}
          <span className="settings-choice-note">
            The server writes these as inline markers ([calm], [excited]); a provider without
            emotion cues ignores them.
          </span>
        </div>

        <div className="settings-group">
          <span className="settings-group-title">Chat model</span>
          {modelChoices.length ? (
            <div className="settings-choices" role="radiogroup" aria-label="model-choice">
              {modelChoices.map((model) => {
                const selected = (prefs.model || modelCatalog.data?.default || "") === model.id;
                return (
                  <button
                    key={model.id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => setVoicePreferences({ model: model.id })}
                    className={`settings-choice${selected ? " is-active" : ""}`}
                  >
                    <span className="settings-choice-label">{model.label}</span>
                    <span className="settings-choice-note">
                      {model.local ? "local" : "cloud"} · {model.reason}
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <span className="settings-choice-note">
              {modelCatalog.isLoading
                ? "Loading models…"
                : "No configured model providers. Add an API key to enable model selection."}
            </span>
          )}
        </div>

        <div className="settings-status">
          <span className="settings-status-row">
            <Mic size={12} />
            {engines.isLoading
              ? "Checking server engines…"
              : engines.data
                ? `Speech recognition: ${prefs.stt} · server ${engines.data.stt.engine}${engines.data.stt.available ? "" : " (unavailable)"}`
                : "Server voice report unavailable."}
          </span>
          <span className="settings-status-row">
            <Volume2 size={12} />
            {engines.data
              ? `Voice output: ${prefs.tts}${prefs.ttsVoice ? ` · ${prefs.ttsVoice}` : ""}${prefs.ttsTags.length ? ` · ${prefs.ttsTags.join(", ")}` : ""} · server ${engines.data.tts.engine}${engines.data.tts.available ? "" : " (unavailable)"}`
              : "—"}
          </span>
        </div>

        <div className="panel-foot">
          <span>Saved in this browser</span>
          <Button variant="ghost" onClick={() => resetVoicePreferences()}>
            <RotateCcw size={12} /> RESET
          </Button>
        </div>
      </section>
    </div>
  );
}
