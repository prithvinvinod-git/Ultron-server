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
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "closed"
  | "error";

export interface VoiceCallbacks {
  onState?: (state: VoiceState) => void;
  onPartial?: (text: string) => void;
  onFinal?: (text: string) => void;
  onAnswer?: (text: string) => void;
  onError?: (message: string) => void;
  /** Microphone level in `[0, 1]`, for a live UI meter. */
  onLevel?: (level: number) => void;
}

/** Target frame length for upload, in seconds. ~20 ms balances latency and chattiness. */
const FRAME_SECONDS = 0.02;
/** Trailing silence that ends an utterance. */
const SILENCE_END_MS = 800;
/** How long speech must persist to open an utterance (avoids clicks). */
const SPEECH_ONSET_MS = 120;
/** Pre-roll retained so the first syllable is not clipped, in seconds. */
const PREROLL_SECONDS = 0.3;

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

  constructor(callbacks: VoiceCallbacks = {}) {
    this.callbacks = callbacks;
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
    this.socket = new WebSocket(wsUrl());
    this.socket.binaryType = "arraybuffer";
    this.socket.onmessage = (event) => this.onMessage(event);
    this.socket.onerror = () => this.fail("the voice connection failed");
    this.socket.onclose = () => {
      this.socket = null;
      if (this.state !== "error") this.setState("closed");
      void this.teardownAudio();
    };
  }

  /** End the session and release the microphone and playback graph. */
  stop(): void {
    this.send({ type: "stop" });
    this.socket?.close();
    this.socket = null;
    void this.teardownAudio();
    this.setState("idle");
  }

  /** Ask the server to stop the current turn (server-side barge-in). */
  interrupt(): void {
    this.flushPlayback();
    this.send({ type: "interrupt" });
  }

  private setState(state: VoiceState): void {
    this.state = state;
    this.callbacks.onState?.(state);
  }

  private fail(message: string): void {
    this.callbacks.onError?.(message);
    this.setState("error");
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
      void this.startCapture();
      return;
    }
    if (type === "state") {
      const state = frame.state;
      if (state === "listening") this.setState("listening");
      else if (state === "thinking") this.setState("thinking");
      else if (state === "speaking") this.setState("speaking");
      return;
    }
    if (type === "stt_partial") {
      this.callbacks.onPartial?.(frame.text ?? "");
      return;
    }
    if (type === "stt_final") {
      this.callbacks.onFinal?.(frame.text ?? "");
      return;
    }
    if (type === "answer") {
      this.callbacks.onAnswer?.(frame.text ?? "");
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
      const url = URL.createObjectURL(new Blob([CAPTURE_WORKLET], { type: "text/javascript" }));
      await this.captureCtx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      const node = new AudioWorkletNode(this.captureCtx, "ultron-capture");
      node.port.onmessage = (event) => this.onSamples(event.data as Float32Array);
      source.connect(node);
      this.silence(node);
      this.captureNode = node;
    } else {
      const node = this.captureCtx.createScriptProcessor(4096, 1, 1);
      node.onaudioprocess = (event) => this.onSamples(event.inputBuffer.getChannelData(0).slice());
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
      if (this.preRoll.length > Math.ceil(PREROLL_SECONDS / FRAME_SECONDS)) this.preRoll.shift();
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
    if (typeof MediaSource === "undefined" || !MediaSource.isTypeSupported(this.ttsContentType)) {
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
    if (this.mediaSource && this.sourceBuffer && !this.sourceBuffer.updating && this.mediaSource.readyState === "open") {
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
