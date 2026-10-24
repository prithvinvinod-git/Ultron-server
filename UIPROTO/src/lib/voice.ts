/**
 * Realtime voice client for the ULTRON `WS /voice/ws` conversation.
 *
 * The server owns all speech logic (utterance boundaries, barge-in, the §59.17
 * partial/final rule); this client is the *transport and the ear/mouth*. It:
 *
 * - captures the microphone through an `AudioWorklet`, resamples to the rate the
 *   STT engine wants (16 kHz), and streams raw little-endian 16-bit PCM up;
 * - runs a small energy VAD so it uploads speech, not silence, and tells the
 *   server when the user stopped talking (`{"type":"utterance_end"}`) -- the
 *   server's engines close the turn on that marker rather than waiting for a run
 *   of silent chunks, so the browser can stream only speech yet still hand off
 *   the floor promptly;
 * - plays the synthesized audio back, streaming: raw PCM (`audio/L16`) through
 *   the Web Audio graph, a compressed container (`audio/mpeg`) through
 *   `MediaSource` when the browser supports it;
 * - barge-in: speaking while ULTRON is talking flushes local playback at once and
 *   tells the server, so the user never has to wait out a sentence.
 *
 * The microphone is only started once the server's `session` frame arrives, so
 * audio is always resampled to the rate the server actually named.
 */

export type VoiceState =
    "idle" | "connecting" | "listening" | "thinking" | "speaking" | "closed" | "error";

export interface VoiceCallbacks {
    onState?: (state: VoiceState) => void;
    onPartial?: (text: string) => void;
    onFinal?: (text: string) => void;
    onAnswer?: (text: string) => void;
    onError?: (message: string) => void;
    /** Microphone level in `[0, 1]`, for a live UI meter. */
    onLevel?: (level: number) => void;
}

/** Where speech-to-text happens: server Whisper, or the browser's own engine. */
export type VoiceSttMode = "server" | "browser";

export interface VoiceClientOptions {
    /**
     * `"server"` streams PCM up for the server's STT engine to transcribe;
     * `"browser"` runs `SpeechRecognition` locally and sends a finished
     * `{"type":"text"}` turn, so no audio is uploaded for recognition.
     */
    stt?: VoiceSttMode;
    /**
     * Browser-STT only. `true` (default) hands each finished phrase to the server
     * as a voice turn; `false` leaves the transcript to the caller (dictation,
     * where the text is used locally instead of run as a voice conversation).
     */
    autoTurn?: boolean;
    /**
     * The server-side speech-to-text engine to use (`groq`, `faster-whisper`).
     * Only meaningful in `"server"` mode: the server transcribes the uploaded
     * audio with the engine the client names (§25.1).
     */
    sttEngine?: string;
    /**
     * The text-to-speech provider to speak with (`fish-audio`, `edge-tts`,
     * `elevenlabs`). Empty keeps the server's configured engine.
     */
    ttsEngine?: string;
    /**
     * The voice within that provider — a voice name, or a cloned voice's
     * reference id. Empty keeps the provider's default.
     */
    ttsVoice?: string;
    /**
     * Delivery cues for the spoken reply (`calm`, `excited`, ...). Fish Audio
     * renders them as inline markers; another provider ignores them. Empty means
     * no cues, which also cancels a deployment-wide default.
     */
    ttsTags?: string[];
}

/** Target frame length for upload, in seconds. ~20 ms balances latency and chattiness. */
const FRAME_SECONDS = 0.02;
/** Trailing silence that ends an utterance. */
const SILENCE_END_MS = 800;
/** How long speech must persist to open an utterance (avoids clicks). */
const SPEECH_ONSET_MS = 120;
/** Pre-roll retained so the first syllable is not clipped, in seconds. */
const PREROLL_SECONDS = 0.3;
/**
 * How long after the last spoken word a transcript is still considered a
 * possible echo of our own voice. Playback drains within a second or two of the
 * final `answer` frame and barge-in flushes it immediately, so once this window
 * has passed any transcript is treated as the user's own speech again. This is
 * what stops the guard from ever swallowing a genuine command.
 */
const ECHO_GRACE_MS = 2500;

const CAPTURE_WORKLET = `
class UltronCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("ultron-capture", UltronCapture);
`;

function wsUrl(): string {
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${scheme}//${window.location.host}/voice/ws`;
}

/**
 * Speak one line directly through the server's TTS engine, without a model turn.
 *
 * The entry greeting (and any one-off line like a notification) should not cost
 * an LLM round-trip, and it must not depend on the voice socket being open yet.
 * This mirrors the reference client's `speak()`: POST the text, play the audio
 * bytes with a plain `Audio` element, and resolve when playback finishes.
 *
 * On any server failure (TTS unavailable, bad params — the route answers 503
 * with `{fallback:"browser"}`) it degrades to the browser's own speech
 * synthesizer, so ULTRON stays audible rather than silent. It returns `false`
 * only when neither path could speak.
 *
 * `engine` names the provider to speak with (`fish-audio` for a cloned voice);
 * empty uses the server's configured engine. `tags` are delivery cues the
 * provider may render (`calm`, `excited`, ...); providers without them ignore
 * the field.
 */
export async function speakText(
    text: string,
    voice?: string,
    engine?: string,
    tags?: readonly string[],
): Promise<boolean> {
    const line = text.trim();
    if (!line) return false;
    try {
        const res = await fetch("/voice/tts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                text: line,
                ...(voice ? { voice } : {}),
                ...(engine ? { engine } : {}),
                ...(tags && tags.length ? { tags: [...tags] } : {}),
            }),
        });
        if (!res.ok) return speakWithBrowser(line);
        const contentType = res.headers.get("Content-Type") ?? "";
        // The route can only send audio; a JSON body means an error slipped through
        // (e.g. a proxy), so do not try to decode it as audio.
        if (contentType.includes("application/json")) return speakWithBrowser(line);
        const blob = await res.blob();
        if (!blob.size) return speakWithBrowser(line);
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        const played = await new Promise<boolean>((resolve) => {
            const done = (ok: boolean) => {
                URL.revokeObjectURL(url);
                resolve(ok);
            };
            audio.onended = () => done(true);
            audio.onerror = () => done(false);
            void audio.play().catch(() => done(false));
        });
        return played || speakWithBrowser(line);
    } catch {
        return speakWithBrowser(line);
    }
}

/**
 * Last-resort speech through the browser's `speechSynthesis`.
 *
 * This is the reference client's documented fallback when the server cannot
 * synthesise. It is silent (returns `false`) on a browser without the API.
 */
function speakWithBrowser(text: string): boolean {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) return false;
    try {
        const utterance = new SpeechSynthesisUtterance(text);
        window.speechSynthesis.speak(utterance);
        return true;
    } catch {
        return false;
    }
}

/** Resample mono float samples to 16-bit little-endian PCM at ``outputRate``. */
function toPcm16(input: Float32Array, inputRate: number, outputRate: number): ArrayBuffer {
    if (inputRate === outputRate) {
        const out = new ArrayBuffer(input.length * 2);
        const view = new DataView(out);
        for (let i = 0; i < input.length; i += 1) {
            const sample = input[i] ?? 0;
            const clamped = Math.max(-1, Math.min(1, sample));
            view.setInt16(i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
        }
        return out;
    }
    const ratio = outputRate / inputRate;
    const length = Math.max(1, Math.floor(input.length * ratio));
    const out = new ArrayBuffer(length * 2);
    const view = new DataView(out);
    for (let i = 0; i < length; i += 1) {
        const position = i / ratio;
        const index = Math.floor(position);
        const next = Math.min(index + 1, input.length - 1);
        const fraction = position - index;
        const head = input[index] ?? 0;
        const tail = input[next] ?? head;
        const sample = head + (tail - head) * fraction;
        const clamped = Math.max(-1, Math.min(1, sample));
        view.setInt16(i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    }
    return out;
}

function rms(samples: Float32Array): number {
    let sum = 0;
    for (let i = 0; i < samples.length; i += 1) {
        const sample = samples[i] ?? 0;
        sum += sample * sample;
    }
    return Math.sqrt(sum / samples.length);
}

/**
 * Split text into comparable words: lowercased, letters/apostrophes only, and
 * only words longer than two characters. Short tokens ("a", "of", "is") carry no
 * signal and would make any two English sentences look alike.
 */
function echoWords(text: string): string[] {
    return text
        .toLowerCase()
        .replace(/[^a-z'\s]/g, " ")
        .split(/\s+/)
        .filter((word) => word.length > 2);
}

/**
 * Echo guard for voice mode (ported from the Ultron reference repo's
 * `looksLikeEcho`). ULTRON's own text-to-speech plays through the speakers and
 * bleeds straight back into the open microphone; speech-to-text then transcribes
 * ULTRON's own words as if the user had said them, and the session answers its
 * own reply forever.
 *
 * A transcript is treated as an echo of `spoken` (the text ULTRON last said)
 * when either:
 *
 *  - **containment** — the whole transcript is a substring of what was spoken,
 *    or the spoken line contains the transcript (a partial re-hear), or
 *  - **overlap** — at least half the transcript's meaningful words already
 *    appear in what was spoken.
 *
 * Returning `true` means "drop this: it is ULTRON hearing itself". It is
 * deliberately asymmetric — a genuine short command ("stop") never overlaps a
 * long spoken paragraph enough to be swallowed, because `echoWords` keeps only
 * words longer than two characters and the ratio is taken over the *transcript*.
 */
export function looksLikeEcho(spoken: string, heard: string): boolean {
    const spokenClean = spoken.trim().toLowerCase();
    const heardClean = heard.trim().toLowerCase();
    if (!spokenClean || !heardClean) return false;

    const heardWords = echoWords(heardClean);
    if (heardWords.length === 0) {
        // Nothing but filler/short words: only an echo if it sits inside the speech.
        return spokenClean.includes(heardClean) || heardClean.includes(spokenClean);
    }

    if (spokenClean.includes(heardClean) || heardClean.includes(spokenClean)) return true;

    const spokenSet = new Set(echoWords(spokenClean));
    let overlap = 0;
    for (const word of heardWords) {
        if (spokenSet.has(word)) overlap += 1;
    }
    return overlap / heardWords.length >= 0.5;
}

/** Minimal shape of the Web Speech API recognition interface. */
interface SpeechRecognitionLike {
    lang: string;
    continuous: boolean;
    interimResults: boolean;
    maxAlternatives: number;
    start(): void;
    stop(): void;
    abort(): void;
    onresult: ((event: SpeechRecognitionEventLike) => void) | null;
    onerror: ((event: { error?: string }) => void) | null;
    onend: (() => void) | null;
}

interface SpeechRecognitionEventLike {
    resultIndex: number;
    results: ArrayLike<{
        isFinal: boolean;
        0: { transcript: string };
    }>;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function speechRecognitionCtor(): SpeechRecognitionCtor | null {
    if (typeof window === "undefined") return null;
    const w = window as unknown as {
        SpeechRecognition?: SpeechRecognitionCtor;
        webkitSpeechRecognition?: SpeechRecognitionCtor;
    };
    return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** A JSON frame sent by `WS /voice/ws` (only the fields this client reads). */
interface ServerFrame {
    type: string;
    state?: string;
    text?: string;
    reason?: string;
    message?: string;
    interrupted?: boolean;
    sample_rate?: number;
    tts?: { sample_rate?: number; content_type?: string | null };
}

export class VoiceClient {
    private readonly callbacks: VoiceCallbacks;
    private readonly sttMode: VoiceSttMode;
    private readonly autoTurn: boolean;
    private sttEngine: string;
    private ttsEngine: string;
    private ttsVoice: string;
    private ttsTags: string[];
    /** Whether the engine choice has already been sent for this session. */
    private configSent = false;
    /** Whether the microphone (or browser recognition) is already running. */
    private listening = false;
    private recognition: SpeechRecognitionLike | null = null;
    private readonly browserTranscript: { final: string } = { final: "" };
    /** Consecutive transient recognition errors (a dead speech service). */
    private browserFaults = 0;
    private socket: WebSocket | null = null;
    private stream: MediaStream | null = null;
    private captureCtx: AudioContext | null = null;
    private captureNode: AudioNode | null = null;
    private playCtx: AudioContext | null = null;
    private playGain: GainNode | null = null;
    private playHead = 0;
    private readonly sources = new Set<AudioBufferSourceNode>();
    private audioEl: HTMLAudioElement | null = null;
    private mediaSource: MediaSource | null = null;
    private sourceBuffer: SourceBuffer | null = null;
    private readonly appendQueue: ArrayBuffer[] = [];
    private readonly mseChunks: ArrayBuffer[] = [];

    private ttsSampleRate = 16000;
    private ttsContentType = "audio/L16";
    private sttSampleRate = 16000;
    private mseSupported = false;
    private mseFailed = false;

    private state: VoiceState = "idle";
    private speaking = false;
    private speechMs = 0;
    private silenceMs = 0;
    private noiseFloor = 0.005;
    private pending: Float32Array[] = [];
    private pendingLength = 0;
    private readonly preRoll: ArrayBuffer[] = [];
    private frameSamples = 0;

    /**
     * The text ULTRON last spoke, accumulated across `answer` frames of the turn,
     * plus a monotonically increasing stamp of when speech was last emitted. The
     * capture path compares each incoming transcript against this to drop ULTRON
     * hearing its own voice (see {@link looksLikeEcho}).
     */
    private spokenText = "";
    private spokenAt = 0;
    /** An utterance that was swallowed as our own echo, for `isEcho` reporting. */
    private lastWasEcho = false;

    constructor(callbacks: VoiceCallbacks = {}, options: VoiceClientOptions = {}) {
        this.callbacks = callbacks;
        this.sttMode = options.stt ?? "server";
        this.autoTurn = options.autoTurn ?? true;
        this.sttEngine = options.sttEngine ?? "";
        this.ttsEngine = options.ttsEngine ?? "";
        this.ttsVoice = options.ttsVoice ?? "";
        this.ttsTags = options.ttsTags ? [...options.ttsTags] : [];
    }

    get currentState(): VoiceState {
        return this.state;
    }

    /**
     * Warm the playback graph from a user gesture.
     *
     * Browsers only let an `AudioContext` start from a user gesture; the context
     * this client needs for playback is created after an async WebSocket
     * round-trip, which is too late to count as one. Calling this synchronously in
     * the button handler unlocks output so the first reply is not silently dropped.
     */
    unlock(): void {
        if (!this.playCtx) this.playCtx = new AudioContext();
        void this.playCtx.resume();
    }

    /** Open the socket and, once the session frame names the rates, the microphone. */
    async start(): Promise<void> {
        if (this.socket) return;
        this.setState("connecting");
        const socket = new WebSocket(wsUrl());
        this.socket = socket;
        socket.binaryType = "arraybuffer";
        socket.onmessage = (event) => this.onMessage(event);
        socket.onerror = () => this.fail("the voice connection failed");
        socket.onclose = () => {
            this.socket = null;
            if (this.state !== "error") this.setState("closed");
            void this.teardownAudio();
        };
        // Resolve only once the socket is actually OPEN so callers can send a turn
        // (e.g. the entry greeting) right after `await start()` without it being
        // silently dropped by `send()`'s readyState guard.
        await new Promise<void>((resolve) => {
            if (socket.readyState === WebSocket.OPEN) {
                resolve();
                return;
            }
            socket.addEventListener("open", () => resolve(), { once: true });
            socket.addEventListener("error", () => resolve(), { once: true });
        });
    }

    /** True while the socket is open and able to carry a turn. */
    get isReady(): boolean {
        return this.socket?.readyState === WebSocket.OPEN;
    }

    /** End the session and release the microphone and playback graph. */
    stop(): void {
        this.send({ type: "stop" });
        this.socket?.close();
        this.socket = null;
        this.listening = false;
        this.configSent = false;
        void this.teardownAudio();
        this.setState("idle");
    }

    /** Ask the server to stop the current turn (server-side barge-in). */
    interrupt(): void {
        this.flushPlayback();
        this.send({ type: "interrupt" });
    }

    /**
     * Change the engine this session speaks and listens with.
     *
     * The choice travels to the server as a `config` frame, so it takes effect on
     * the next turn — no restart, no new socket. The server answers with a fresh
     * `session` frame, which carries the new engine's rate and media type.
     */
    setEngines(selection: {
        sttEngine?: string;
        ttsEngine?: string;
        ttsVoice?: string;
        ttsTags?: string[];
    }): void {
        if (selection.sttEngine !== undefined) this.sttEngine = selection.sttEngine;
        if (selection.ttsEngine !== undefined) this.ttsEngine = selection.ttsEngine;
        if (selection.ttsVoice !== undefined) this.ttsVoice = selection.ttsVoice;
        if (selection.ttsTags !== undefined) this.ttsTags = [...selection.ttsTags];
        this.configSent = false;
        this.sendConfig();
    }

    /** Send a text turn directly (for greetings, etc.). */
    sendText(text: string, voice?: string): void {
        this.setState("thinking");
        const payload: Record<string, unknown> = { type: "text", text };
        const spoken = voice || this.ttsVoice;
        if (spoken) payload["voice"] = spoken;
        if (this.ttsEngine) {
            payload["tts"] = {
                engine: this.ttsEngine,
                voice: spoken ?? "",
                tags: this.ttsTags,
            };
        }
        this.send(payload);
    }

    /**
     * Tell the server which engines this session uses.
     *
     * Sent once per session: the server replies with a `session` frame, which
     * must not trigger another config frame (that would loop).
     */
    private sendConfig(): void {
        if (this.configSent) return;
        if (this.socket?.readyState !== WebSocket.OPEN) return;
        const payload: Record<string, unknown> = { type: "config" };
        if (this.sttMode === "server" && this.sttEngine) payload["stt"] = this.sttEngine;
        if (this.ttsEngine) {
            payload["tts"] = {
                engine: this.ttsEngine,
                voice: this.ttsVoice,
                tags: this.ttsTags,
            };
        }
        this.configSent = true;
        this.send(payload);
    }

    private setState(state: VoiceState): void {
        this.state = state;
        this.callbacks.onState?.(state);
    }

    private fail(message: string): void {
        this.callbacks.onError?.(message);
        this.setState("error");
    }

    /**
     * True when `text` is (probably) ULTRON hearing its own reply rather than the
     * user speaking. Only ever true inside {@link ECHO_GRACE_MS} of the last word
     * we spoke, so a genuine command given after the reply finishes is never
     * swallowed; inside that window it defers to {@link looksLikeEcho}.
     */
    private isEcho(text: string): boolean {
        if (!this.spokenText.trim() || !text.trim()) return false;
        if (Date.now() - this.spokenAt > ECHO_GRACE_MS) return false;
        return looksLikeEcho(this.spokenText, text);
    }

    private send(payload: Record<string, unknown>): void {
        if (this.socket?.readyState === WebSocket.OPEN) {
            this.socket.send(JSON.stringify(payload));
        }
    }

    private onMessage(event: MessageEvent): void {
        if (event.data instanceof ArrayBuffer) {
            this.onAudio(event.data);
            return;
        }
        if (typeof event.data !== "string") return;
        let frame: ServerFrame;
        try {
            frame = JSON.parse(event.data) as ServerFrame;
        } catch {
            return;
        }
        this.onFrame(frame);
    }

    private onFrame(frame: ServerFrame): void {
        const type = frame.type;
        if (type === "session") {
            const tts = frame.tts;
            this.sttSampleRate = frame.sample_rate ?? 16000;
            this.ttsSampleRate = tts?.sample_rate ?? 22050;
            this.ttsContentType = tts?.content_type ?? "audio/L16";
            // Name the engines this session uses, then start listening. The
            // server answers with a fresh session frame carrying the rates.
            this.sendConfig();
            if (!this.listening) {
                this.listening = true;
                if (this.sttMode === "browser") this.startBrowserRecognition();
                else void this.startCapture();
            }
            return;
        }
        if (type === "state") {
            const state = frame.state;
            if (state === "listening") {
                // A new turn is opening: the previous reply is no longer "in the air",
                // so stop treating its words as a possible echo.
                this.spokenText = "";
                this.speaking = false;
                this.setState("listening");
            } else if (state === "thinking") this.setState("thinking");
            else if (state === "speaking") this.setState("speaking");
            return;
        }
        if (type === "stt_partial") {
            const text = frame.text ?? "";
            // Hold back partials of our own speech so the subtitle never flickers with
            // a half-heard copy of the reply we are still reading out.
            if (!this.isEcho(text)) this.callbacks.onPartial?.(text);
            return;
        }
        if (type === "stt_final") {
            const text = frame.text ?? "";
            this.lastWasEcho = this.isEcho(text);
            if (!this.lastWasEcho) this.callbacks.onFinal?.(text);
            return;
        }
        if (type === "answer") {
            // Accumulate what ULTRON is saying so the echo guard has a reference, and
            // stamp the moment so the window stays current as playback streams.
            const text = frame.text ?? "";
            if (text) {
                this.spokenText = `${this.spokenText} ${text}`.trim();
                this.spokenAt = Date.now();
            }
            this.callbacks.onAnswer?.(text);
            return;
        }
        if (type === "tts_completed") {
            if (frame.interrupted) this.flushPlayback();
            else if (!this.mseSupported && !this.ttsContentType.startsWith("audio/L16")) {
                void this.decodeBuffered();
            }
            return;
        }
        if (type === "ended") {
            this.setState("closed");
            return;
        }
        if (type === "error") {
            this.fail(frame.message ?? "voice error");
        }
    }

    // -- capture ---------------------------------------------------------------

    /**
     * Local speech-to-text through the browser's own recognition engine.
     *
     * Recognition runs locally (no audio leaves the device for this step), and a
     * finished phrase is handed to the server as a `{"type":"text"}` turn so it
     * runs the exact same agent + TTS path the server-STT flow does. `continuous`
     * with auto-restart keeps the session live across phrases.
     */
    private startBrowserRecognition(): void {
        const Ctor = speechRecognitionCtor();
        if (!Ctor) {
            this.fail("this browser has no speech recognition; choose the server STT engine");
            return;
        }
        const recognition = new Ctor();
        recognition.lang = "en-US";
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.maxAlternatives = 1;

        recognition.onerror = (event) => {
            // "no-speech" and "aborted" are routine. "network" is a Chrome cloud
            // recognition failure, but it is also what Chromium *always* returns
            // when the speech service has no credentials (Electron, some
            // distributions). Restart a couple of times, then say so instead of
            // restarting forever into silence.
            if (!event.error || event.error === "no-speech" || event.error === "aborted") return;
            if (event.error === "network") {
                this.browserFaults += 1;
                if (this.browserFaults >= 2) {
                    this.fail(
                        "browser speech recognition is unavailable here; choose a server speech-to-text engine in Settings",
                    );
                }
                return;
            }
            this.fail(`speech recognition failed: ${event.error}`);
        };
        recognition.onresult = (event) => {
            this.browserFaults = 0;
            let interim = "";
            for (let i = event.resultIndex; i < event.results.length; i += 1) {
                const result = event.results[i];
                if (!result) continue;
                const transcript = result[0]?.transcript ?? "";
                if (result.isFinal) {
                    this.browserTranscript.final += transcript;
                } else {
                    interim += transcript;
                }
            }
            const combined = `${this.browserTranscript.final}${interim}`.trim();
            // If this is ULTRON hearing its own reply, ignore it entirely: no partial,
            // no barge-in (we would interrupt ourselves), no turn.
            const echo = this.isEcho(combined);
            if (combined && !echo) this.callbacks.onPartial?.(combined);
            // Speaking over ULTRON is a barge-in, same as the audio VAD path (§59.19).
            if (combined && !echo && (this.state === "thinking" || this.state === "speaking")) {
                this.interrupt();
            }
            const finalText = this.browserTranscript.final.trim();
            if (finalText) {
                this.browserTranscript.final = "";
                this.lastWasEcho = this.isEcho(finalText);
                if (this.lastWasEcho) return;
                this.callbacks.onFinal?.(finalText);
                if (this.autoTurn) {
                    this.setState("thinking");
                    this.send({ type: "text", text: finalText });
                }
            }
        };
        recognition.onend = () => {
            // Chrome stops recognition periodically; restart while the socket is open.
            if (this.recognition === recognition && this.socket) {
                try {
                    recognition.start();
                } catch {
                    /* already starting — ignore */
                }
            }
        };

        this.recognition = recognition;
        try {
            recognition.start();
        } catch {
            this.fail("could not start browser speech recognition");
            return;
        }
        this.setState("listening");
    }

    private async startCapture(): Promise<void> {
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                },
            });
        } catch {
            this.fail("microphone permission was denied");
            return;
        }
        const Ctor = window.AudioContext;
        this.captureCtx = new Ctor();
        await this.captureCtx.resume();
        this.frameSamples = Math.round(this.captureCtx.sampleRate * FRAME_SECONDS);
        const source = this.captureCtx.createMediaStreamSource(this.stream);

        if (this.captureCtx.audioWorklet) {
            const url = URL.createObjectURL(
                new Blob([CAPTURE_WORKLET], { type: "text/javascript" }),
            );
            await this.captureCtx.audioWorklet.addModule(url);
            URL.revokeObjectURL(url);
            const node = new AudioWorkletNode(this.captureCtx, "ultron-capture");
            node.port.onmessage = (event) => this.onSamples(event.data as Float32Array);
            source.connect(node);
            this.silence(node);
            this.captureNode = node;
        } else {
            const node = this.captureCtx.createScriptProcessor(4096, 1, 1);
            node.onaudioprocess = (event) =>
                this.onSamples(event.inputBuffer.getChannelData(0).slice());
            source.connect(node);
            this.silence(node);
            this.captureNode = node;
        }
        this.setState("listening");
    }

    /** Connect a node to the graph (through a muted gain) so its `process` runs. */
    private silence(node: AudioNode): void {
        if (!this.captureCtx) return;
        const mute = this.captureCtx.createGain();
        mute.gain.value = 0;
        node.connect(mute);
        mute.connect(this.captureCtx.destination);
    }

    private onSamples(samples: Float32Array): void {
        if (!this.captureCtx) return;
        const level = rms(samples);
        this.callbacks.onLevel?.(Math.min(1, level * 4));
        const speech = level > Math.max(this.noiseFloor * 3, 0.012);
        if (!speech) this.noiseFloor = this.noiseFloor * 0.95 + level * 0.05;

        const blockMs = (samples.length / this.captureCtx.sampleRate) * 1000;
        if (speech) {
            this.speechMs += blockMs;
            this.silenceMs = 0;
        } else {
            this.silenceMs += blockMs;
            this.speechMs = 0;
        }

        this.pending.push(samples);
        this.pendingLength += samples.length;
        const ready: Float32Array[] = [];
        while (this.pendingLength >= this.frameSamples) {
            ready.push(this.takeFrame());
        }
        for (const frame of ready) this.emit(frame, speech);

        const wasSpeaking = this.speaking;
        if (!this.speaking && this.speechMs >= SPEECH_ONSET_MS) {
            this.speaking = true;
        }
        if (this.speaking && this.silenceMs >= SILENCE_END_MS) {
            this.speaking = false;
            this.speechMs = 0;
            this.send({ type: "utterance_end" });
        }
        if (!wasSpeaking && this.speaking) {
            // A new utterance while ULTRON is talking is a barge-in.
            if (this.state === "thinking" || this.state === "speaking") this.interrupt();
            for (const buffered of this.preRoll) this.sendBytes(buffered);
            this.preRoll.length = 0;
        }
    }

    private takeFrame(): Float32Array {
        const frame = new Float32Array(this.frameSamples);
        let filled = 0;
        while (filled < this.frameSamples && this.pending.length) {
            const head = this.pending[0];
            if (!head) break;
            const take = Math.min(head.length, this.frameSamples - filled);
            frame.set(head.subarray(0, take), filled);
            filled += take;
            if (take === head.length) this.pending.shift();
            else this.pending[0] = head.subarray(take);
        }
        this.pendingLength -= this.frameSamples;
        return frame;
    }

    private emit(frame: Float32Array, speech: boolean): void {
        if (!this.captureCtx) return;
        const pcm = toPcm16(frame, this.captureCtx.sampleRate, this.sttSampleRate);
        if (this.speaking && (speech || this.silenceMs < SILENCE_END_MS)) {
            this.sendBytes(pcm);
        } else {
            this.preRoll.push(pcm);
            if (this.preRoll.length > Math.ceil(PREROLL_SECONDS / FRAME_SECONDS))
                this.preRoll.shift();
        }
    }

    private sendBytes(pcm: ArrayBuffer): void {
        if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(pcm);
    }

    // -- playback --------------------------------------------------------------

    private onAudio(data: ArrayBuffer): void {
        if (this.ttsContentType.startsWith("audio/L16") || this.ttsContentType === "audio/pcm") {
            this.playPcm(data);
            return;
        }
        if (this.tryInitMse()) {
            this.appendQueue.push(data);
            this.drainAppend();
            return;
        }
        // No MediaSource: buffer and decode the whole turn when it completes.
        this.mseChunks.push(data);
    }

    private playPcm(data: ArrayBuffer): void {
        if (!this.playCtx) this.playCtx = new AudioContext();
        if (!this.playGain) {
            this.playGain = this.playCtx.createGain();
            this.playGain.connect(this.playCtx.destination);
        }
        void this.playCtx.resume();
        const count = Math.floor(data.byteLength / 2);
        if (count === 0) return;
        const view = new DataView(data);
        const buffer = this.playCtx.createBuffer(1, count, this.ttsSampleRate);
        const channel = buffer.getChannelData(0);
        for (let i = 0; i < count; i += 1) channel[i] = view.getInt16(i * 2, true) / 32768;
        const source = this.playCtx.createBufferSource();
        source.buffer = buffer;
        source.connect(this.playGain);
        const start = Math.max(this.playHead, this.playCtx.currentTime);
        source.start(start);
        this.playHead = start + buffer.duration;
        this.sources.add(source);
        source.onended = () => this.sources.delete(source);
    }

    private tryInitMse(): boolean {
        if (this.mseSupported) return true;
        if (this.mseFailed) return false;
        if (
            typeof MediaSource === "undefined" ||
            !MediaSource.isTypeSupported(this.ttsContentType)
        ) {
            this.mseFailed = true;
            return false;
        }
        this.audioEl = new Audio();
        this.mediaSource = new MediaSource();
        this.audioEl.src = URL.createObjectURL(this.mediaSource);
        this.mediaSource.addEventListener("sourceopen", () => {
            if (!this.mediaSource) return;
            try {
                this.sourceBuffer = this.mediaSource.addSourceBuffer(this.ttsContentType);
                this.sourceBuffer.mode = "sequence";
                this.sourceBuffer.addEventListener("updateend", () => this.drainAppend());
                this.drainAppend();
                void this.audioEl?.play().catch(() => undefined);
            } catch {
                this.mseFailed = true;
                this.mseSupported = false;
            }
        });
        this.mseSupported = true;
        return true;
    }

    private drainAppend(): void {
        const buffer = this.sourceBuffer;
        if (!buffer || buffer.updating || this.appendQueue.length === 0) return;
        const next = this.appendQueue.shift();
        if (!next) return;
        try {
            buffer.appendBuffer(next);
        } catch {
            // Quota or format error: drop the chunk rather than stall the stream.
            this.drainAppend();
        }
    }

    private flushPlayback(): void {
        for (const source of this.sources) {
            try {
                source.stop();
            } catch {
                // Already ended.
            }
        }
        this.sources.clear();
        if (this.playCtx) this.playHead = this.playCtx.currentTime;
        this.appendQueue.length = 0;
        this.mseChunks.length = 0;
        if (this.audioEl) {
            this.audioEl.pause();
            this.audioEl.currentTime = 0;
        }
        if (
            this.mediaSource &&
            this.sourceBuffer &&
            !this.sourceBuffer.updating &&
            this.mediaSource.readyState === "open"
        ) {
            try {
                this.sourceBuffer.abort();
            } catch {
                // Nothing buffered.
            }
        }
    }

    private async decodeBuffered(): Promise<void> {
        if (this.mseChunks.length === 0) return;
        const total = this.mseChunks.reduce((sum, part) => sum + part.byteLength, 0);
        const merged = new Uint8Array(total);
        let offset = 0;
        for (const part of this.mseChunks) {
            merged.set(new Uint8Array(part), offset);
            offset += part.byteLength;
        }
        this.mseChunks.length = 0;
        if (!this.playCtx) this.playCtx = new AudioContext();
        try {
            const decoded = await this.playCtx.decodeAudioData(merged.buffer);
            const source = this.playCtx.createBufferSource();
            source.buffer = decoded;
            source.connect(this.playCtx.destination);
            source.start();
            this.sources.add(source);
            source.onended = () => this.sources.delete(source);
        } catch {
            this.callbacks.onError?.("could not decode the audio reply");
        }
    }

    private async teardownAudio(): Promise<void> {
        this.flushPlayback();
        if (this.recognition) {
            this.recognition.onend = null;
            this.recognition.onresult = null;
            this.recognition.onerror = null;
            try {
                this.recognition.stop();
            } catch {
                /* already stopped */
            }
            this.recognition = null;
            this.browserTranscript.final = "";
        }
        this.captureNode?.disconnect();
        this.captureNode = null;
        if (this.captureCtx) {
            await this.captureCtx.close();
            this.captureCtx = null;
        }
        this.stream?.getTracks().forEach((track) => track.stop());
        this.stream = null;
        if (this.playCtx) {
            await this.playCtx.close();
            this.playCtx = null;
            this.playGain = null;
        }
        if (this.audioEl) {
            this.audioEl.src = "";
            this.audioEl = null;
        }
        this.mediaSource = null;
        this.sourceBuffer = null;
        this.pending = [];
        this.pendingLength = 0;
        this.preRoll.length = 0;
    }
}
