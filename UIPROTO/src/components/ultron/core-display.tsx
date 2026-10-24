import logo from '@/assets/ultron-logo.png';
import { Activity, Cpu, Mic, ShieldCheck, Square, Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

const polar = (radius: number, angle: number) => ({ x: 300 + radius * Math.cos(angle * Math.PI / 180), y: 300 + radius * Math.sin(angle * Math.PI / 180) });
const arc = (radius: number, start: number, end: number) => {
  const a = polar(radius, start); const b = polar(radius, end);
  return `M${a.x},${a.y} A${radius},${radius} 0 ${end - start > 180 ? 1 : 0} 1 ${b.x},${b.y}`;
};

interface VoiceInlineState {
  status: 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';
  sttPartial: string;
  captions: Array<{ id: string; text: string }>;
  level: number;
}

export function CoreDisplay({
  online,
  paused,
  voice,
  voiceState,
  mode,
  onToggleVoice,
  voiceInline,
}: {
  online: boolean;
  paused: boolean;
  voice: boolean;
  voiceState?: string;
  mode: string;
  onToggleVoice: () => void;
  /** When voice is active, render the inline STT + response area inside the workspace. */
  voiceInline?: VoiceInlineState;
}) {
  const voiceLabel = !voice
    ? 'VOICE MODE'
    : voiceState === 'connecting' ? 'CONNECTING'
    : voiceState === 'thinking' ? 'PROCESSING'
    : voiceState === 'speaking' ? 'SPEAKING'
    : 'LISTENING';

  if (voice && voiceInline) {
    const statusText =
      voiceInline.status === 'listening' ? 'LISTENING'
      : voiceInline.status === 'thinking' ? 'THINKING'
      : voiceInline.status === 'speaking' ? 'SPEAKING'
      : voiceInline.status === 'error' ? 'ERROR' : 'IDLE';
    return (
      <div className={cn('core-display voice-inline', online ? '' : 'core-offline', paused ? 'motion-paused' : '')}>
        <div className="core-coordinate coordinate-top">SYS.01 <span>NEURAL ORCHESTRATOR</span> 006.24</div>
        <div className="voice-arc-stage">
          <svg className="core-art voice-arc" viewBox="0 0 600 600" aria-hidden="true">
            <defs>
              <filter id="core-glow"><feGaussianBlur stdDeviation="3" /></filter>
              <pattern id="core-grid" width="19" height="19" patternUnits="userSpaceOnUse"><path d="M19 0H0V19" className="grid-line" /></pattern>
            </defs>
            <circle cx="300" cy="300" r="171" fill="url(#core-grid)" opacity=".32" />
            <g className="core-guide">
              <circle cx="300" cy="300" r="282" strokeDasharray="1 8" />
              <circle cx="300" cy="300" r="248" strokeDasharray="90 40 10 40" />
              <circle cx="300" cy="300" r="207" />
              <circle cx="300" cy="300" r="147" strokeDasharray="2 7" />
            </g>
            <g className="orbit-slow">
              {Array.from({ length: 120 }, (_, i) => { const a = polar(i % 5 === 0 ? 263 : 270, i * 3); const b = polar(277, i * 3); return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={i % 5 === 0 ? 'tick-major' : 'tick-minor'} />; })}
            </g>
            <g className="orbit-reverse core-guide bold-arcs">
              <path d={arc(259, -82, -18)} /><path d={arc(259, 5, 62)} /><path d={arc(259, 95, 153)} /><path d={arc(259, 181, 237)} />
            </g>
            <g className="glowing-ring">
              <circle cx="300" cy="300" r="191" className="ring-glow" filter="url(#core-glow)" />
              <circle cx="300" cy="300" r="191" className="ring-light" />
              <circle cx="300" cy="300" r="185" className="ring-dashed orbit-fast" />
              {Array.from({ length: 72 }, (_, i) => { const a = polar(194, i * 5); const b = polar(i % 3 === 0 ? 203 : 199, i * 5); return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="tick-cyan" />; })}
              <path d={arc(164, -90, 14)} className="inner-arc" /><path d={arc(164, 90, 194)} className="inner-arc" />
            </g>
          </svg>
          <div className="core-center voice-arc-center">
            <div className="core-emblem"><img src={logo} alt="Ultron logo" /></div>
            <div className="voice-arc-status"><span className={cn('voice-status-dot', voiceInline.status)} />{statusText}</div>
            <Button variant="ghost" className={cn('core-action core-voice', 'is-stop')} onClick={onToggleVoice} aria-pressed={true} aria-label="Stop voice mode"><Square />STOP VOICE</Button>
          </div>
        </div>

        {voiceInline.sttPartial && (
          <div className="voice-stt-line">“{voiceInline.sttPartial}”</div>
        )}

        <div className="voice-response-area" role="log" aria-live="polite">
          {voiceInline.captions.length === 0 ? (
            <div className="voice-response-empty">Ask me anything — I'll answer here.</div>
          ) : (
            voiceInline.captions.map((c, i) => (
              <div key={c.id} className={cn('voice-caption', i === voiceInline.captions.length - 1 ? 'last' : 'prev')}>
                {c.text}
              </div>
            ))
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={cn('core-display', online ? '' : 'core-offline', paused ? 'motion-paused' : '', voice ? 'voice-on' : '')}>
      <div className="core-coordinate coordinate-top">SYS.01 <span>NEURAL ORCHESTRATOR</span> 006.24</div>
      <svg className="core-art" viewBox="0 0 600 600" aria-hidden="true">
        <defs>
          <filter id="core-glow"><feGaussianBlur stdDeviation="3" /></filter>
          <pattern id="core-grid" width="19" height="19" patternUnits="userSpaceOnUse"><path d="M19 0H0V19" className="grid-line" /></pattern>
        </defs>
        <circle cx="300" cy="300" r="171" fill="url(#core-grid)" opacity=".32" />
        <g className="core-guide">
          <path d="M300 8V38M300 562V592M8 300H38M562 300H592" />
          <circle cx="300" cy="300" r="282" strokeDasharray="1 8" />
          <circle cx="300" cy="300" r="260" />
          <circle cx="300" cy="300" r="248" strokeDasharray="90 40 10 40" />
          <circle cx="300" cy="300" r="207" />
          <circle cx="300" cy="300" r="172" />
          <circle cx="300" cy="300" r="147" strokeDasharray="2 7" />
        </g>
        <g className="orbit-slow">
          {Array.from({ length: 120 }, (_, i) => { const a = polar(i % 5 === 0 ? 263 : 270, i * 3); const b = polar(277, i * 3); return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={i % 5 === 0 ? 'tick-major' : 'tick-minor'} />; })}
        </g>
        <g className="orbit-reverse core-guide bold-arcs">
          <path d={arc(259, -82, -18)} /><path d={arc(259, 5, 62)} /><path d={arc(259, 95, 153)} /><path d={arc(259, 181, 237)} />
        </g>
        {Array.from({ length: 16 }, (_, i) => {
          const start = i * 22.5 - 89; const end = start + 18.5; const a = polar(216, start); const b = polar(242, start); const c = polar(242, end); const d = polar(216, end); const label = polar(229, start + 9.25);
          return <g key={i}><path className={cn('core-segment', i === 0 || i === 6 || i === 12 ? 'segment-lit' : '')} d={`M${a.x},${a.y} L${b.x},${b.y} A242,242 0 0 1 ${c.x},${c.y} L${d.x},${d.y} A216,216 0 0 0 ${a.x},${a.y} Z`} /><text x={label.x} y={label.y} transform={`rotate(${start + 99.25},${label.x},${label.y})`} className="segment-label">{['NEURAL', 'SYNC', 'MEM', 'LINK', 'CACHE', 'DATA', 'CORE', 'CPU', 'I/O', 'CTRL', 'NODE', 'FLOW', 'ACT', 'NET', 'SEC', 'SYS'][i]}</text></g>;
        })}
        <g className="glowing-ring">
          <circle cx="300" cy="300" r="191" className="ring-glow" filter="url(#core-glow)" />
          <circle cx="300" cy="300" r="191" className="ring-light" />
          <circle cx="300" cy="300" r="185" className="ring-dashed orbit-fast" />
          {Array.from({ length: 72 }, (_, i) => { const a = polar(194, i * 5); const b = polar(i % 3 === 0 ? 203 : 199, i * 5); return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="tick-cyan" />; })}
          <path d={arc(164, -90, 14)} className="inner-arc" /><path d={arc(164, 90, 194)} className="inner-arc" />
        </g>
        <g className="core-guide"><path d="M300 122V138M300 462V478M122 300H138M462 300H478" />
        <path d="M208 196H230M370 196H392M208 404H230M370 404H392" /></g>
        <text x="300" y="158" className="technical-label">// SYSTEM INTEGRITY: {online ? 'OPTIMAL' : 'STANDBY'}</text>
        <text x="300" y="449" className="technical-label">ULTRON PROTOCOL · V.4.0.2</text>
        {[45,135,225,315].map((angle,i) => { const p = polar(260,angle); return <g key={angle}><circle cx={p.x} cy={p.y} r="9" className="orbit-node" /><circle cx={p.x} cy={p.y} r="2" className="orbit-dot" /></g>; })}
      </svg>
      <div className="core-center">
        <div className="core-emblem"><img src={logo} alt="Ultron logo" /></div>
        <span className="core-eyebrow">ULTRON CORE</span>
        <div className="core-state">{online ? 'ONLINE' : 'STANDBY'}</div>
        <div className="core-status"><span className={cn('status-dot', online ? '' : 'inactive')} />{online ? 'All systems operational' : 'Core is suspended'}</div>
        <Button variant="ghost" className={cn('core-action core-voice', voice ? 'is-listening' : '')} onClick={onToggleVoice} aria-pressed={voice} aria-label={voice ? 'Stop voice mode' : 'Start voice mode'}><Mic />{voiceLabel}</Button>
        <div className="core-mode">{mode.toUpperCase()} PROTOCOL</div>
      </div>
      <div className="core-coordinate coordinate-bottom"><span>ENCRYPTED CONNECTION</span><ShieldCheck size={12} /> AES-256</div>
    </div>
  );
}

export interface CoreMetricsProps {
  agentActive: number;
  agentTotal: number;
  taskOpen: number;
  latencyMs: number;
}

export function CoreMetrics({ agentActive, agentTotal, taskOpen, latencyMs }: CoreMetricsProps) {
  const metrics = [
    { icon: Cpu, value: `${agentActive}`, unit: `/${agentTotal}`, label: 'AGENTS ACTIVE' },
    { icon: Activity, value: `${taskOpen}`, unit: '', label: 'OPEN TASKS' },
    { icon: Zap, value: `${Math.round(latencyMs)}`, unit: 'ms', label: 'HEALTH LATENCY' },
  ];
  return <div className="core-metrics">{metrics.map(item => <div key={item.label}><item.icon /><span className="metric-value">{item.value}<small>{item.unit}</small></span><span className="metric-label">{item.label}</span></div>)}</div>;
}
