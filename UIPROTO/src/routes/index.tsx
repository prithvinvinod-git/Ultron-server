import { createFileRoute } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { BatteryFull, Box, Check, ChevronRight, Code2, Cpu, FileText, Folder, Globe, Grid2X2, Headphones, Layers, Settings2, ShieldCheck, Terminal, Wifi, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { AgentsPanel } from '@/components/ultron/agents';
import { CommandConsole } from '@/components/ultron/command-console';
import { CoreDisplay, CoreMetrics } from '@/components/ultron/core-display';
import { EventsPanel, HealthPanel, NetworkPanel, SessionPanel, TaskPanel, ToolsPanel } from '@/components/ultron/widgets';
import { useAuth } from '@/lib/auth';
import { useEventStream } from '@/lib/events';
import { voiceApi } from '@/lib/api';
import { VoiceClient } from '@/lib/voice';
import type { VoiceState } from '@/lib/voice';
import { useAgents, useHealth, useLiveInvalidation, useTasks, useTools } from '@/lib/queries';

export const Route = createFileRoute('/')({
  head: () => ({ meta: [
    { title: 'Ultron — Core Command Center' },
    { name: 'description', content: 'Ultron Core System: a live command center for your workspace, system insights, and daily missions.' },
    { property: 'og:title', content: 'Ultron — Core Command Center' },
    { property: 'og:description', content: 'A next-generation neural command center with a circular core, system telemetry, and personal workspace tools.' },
    { property: 'og:type', content: 'website' },
    { name: 'twitter:card', content: 'summary_large_image' },
  ] }),
  component: Index,
});

const ACTIVE_AGENT_STATUSES = new Set(['created', 'initializing', 'ready', 'running', 'waiting', 'verifying']);
const OPEN_TASK_STATUSES = new Set(['pending', 'queued', 'running', 'blocked', 'paused']);

function Index() {
  useLiveInvalidation();
  const { state: streamState, events } = useEventStream();
  const { principal } = useAuth();
  const health = useHealth();
  const agents = useAgents();
  const tasks = useTasks();
  const tools = useTools();

  const [paused, setPaused] = useState(false); const [mode, setMode] = useState('Focus');
  const [voice, setVoice] = useState(false);
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [voicePartial, setVoicePartial] = useState(''); const [voiceFinal, setVoiceFinal] = useState('');
  const [voiceAnswer, setVoiceAnswer] = useState(''); const [voiceError, setVoiceError] = useState<string | null>(null);
  const voiceRef = useRef<VoiceClient | null>(null);
  const [tool, setTool] = useState<string | null>(null); const [clock, setClock] = useState('--:--:--');
  const dialogRef = useRef<HTMLDivElement>(null); const triggerRef = useRef<Element | null>(null);

  useEffect(() => () => { voiceRef.current?.stop(); voiceRef.current = null; }, []);

  async function toggleVoice() {
    if (voice) {
      voiceRef.current?.stop(); voiceRef.current = null; setVoice(false); setVoiceState('idle'); return;
    }
    setVoiceError(null); setVoicePartial(''); setVoiceFinal(''); setVoiceAnswer('');
    try {
      const engines = await voiceApi.engines();
      if (!engines.enabled || !engines.stt.available) {
        setVoiceError(engines.stt.reason ?? (engines.enabled ? 'Speech-to-text is unavailable on the server.' : 'Voice is disabled on the server.'));
        return;
      }
      const client = new VoiceClient({
        onState: state => { setVoiceState(state); if (state === 'closed') { setVoice(false); voiceRef.current = null; } },
        onPartial: setVoicePartial,
        onFinal: text => { setVoiceFinal(text); setVoicePartial(''); },
        onAnswer: setVoiceAnswer,
        onError: message => { setVoiceError(message); setVoice(false); voiceRef.current = null; },
      });
      voiceRef.current = client;
      client.unlock();
      setVoice(true);
      await client.start();
    } catch (error) {
      setVoiceError(error instanceof Error ? error.message : 'Could not start voice mode.');
      setVoice(false); voiceRef.current = null;
    }
  }

  useEffect(() => { const update = () => setClock(new Date().toLocaleTimeString('en-GB', { hour12: false })); update(); const timer = setInterval(update, 1000); return () => clearInterval(timer); }, []);
  useEffect(() => { if (!tool) return; triggerRef.current = document.activeElement; dialogRef.current?.focus(); const onKey = (event: KeyboardEvent) => { if(event.key === 'Escape') setTool(null); if(event.key === 'Tab' && dialogRef.current) { const elements = dialogRef.current.querySelectorAll<HTMLElement>('button, input, textarea, [tabindex="0"]'); const first = elements[0]; const last = elements[elements.length-1]; if(event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) {event.preventDefault();last?.focus();} else if(!event.shiftKey && document.activeElement === last){event.preventDefault();first?.focus();} } }; document.addEventListener('keydown',onKey); return () => { document.removeEventListener('keydown',onKey); if(triggerRef.current instanceof HTMLElement) triggerRef.current.focus(); }; }, [tool]);

  const agentList = agents.data ?? [];
  const taskList = tasks.data ?? [];
  const toolList = tools.data ?? [];
  const readiness = health.data?.readiness;
  const online = readiness?.ready ?? streamState === 'open';
  const activeAgents = agentList.filter(agent => ACTIVE_AGENT_STATUSES.has(agent.status)).length;
  const openTasks = taskList.filter(task => OPEN_TASK_STATUSES.has(task.status)).length;
  const latencyMs = readiness?.duration_ms ?? 0;

  const dock = [{icon:Grid2X2,label:'Command center'},{icon:Folder,label:'Documents'},{icon:Globe,label:'Network'},{icon:Terminal,label:'System terminal'},{icon:Headphones,label:'Audio'},{icon:Layers,label:'Workspace'},{icon:Settings2,label:'Core settings'}];

  function renderDialog() {
    if (!tool) return null;
    const key = tool.toLowerCase();
    if (key.includes('settings')) return <><div className="setting-row"><span>Ambient motion</span><Button variant="secondary" size="sm" aria-pressed={!paused} onClick={()=>setPaused(!paused)}>{paused ? 'Paused' : 'Active'}</Button></div><div className="setting-row"><span>Connection</span><span className="text-primary">{streamState.toUpperCase()}</span></div><div className="setting-row"><span>Protocol</span><span className="text-primary">{mode}</span></div><div className="setting-row"><span>Core power</span><Button variant="secondary" size="sm" aria-pressed={online} disabled>{online ? 'Online' : 'Standby'}</Button></div></>;
    if (key === 'diagnostics' || key === 'network') return <>{readiness ? <><div className="dialog-status"><span className={`status-dot${readiness.ready ? '' : ' inactive'}`} />{key === 'network' ? `${typeof window === 'undefined' ? 'localhost' : window.location.host} · ${streamState}` : `Diagnostic scan · ${readiness.status}`}</div><div className="terminal-output">{readiness.checks.map(check => <div key={check.name}>{check.status === 'ok' ? '✓' : '•'} {check.name}: {check.status} · {check.latency_ms.toFixed(0)}ms{check.required ? ' (required)' : ''}</div>)}</div></> : <div className="terminal-output">health unavailable</div>}</>;
    if (key === 'system terminal') return <div className="terminal-output">ULTRON CORE SYSTEM<br/>Use the command console below to talk to the core.<br/><span className="text-muted-foreground">{events.length} events on the bus · stream {streamState}</span></div>;
    if (key === 'documents' || key === 'workspace') return <div className="dialog-files">{taskList.length === 0 ? <div className="panel-empty">no tasks</div> : taskList.slice(0, 8).map(task => <Button variant="ghost" className="justify-start" key={task.id} onClick={()=>navigator.clipboard?.writeText(task.id)}><FileText />{task.goal}<ChevronRight className="ml-auto" /></Button>)}</div>;
    if (key === 'notifications') return <div className="dialog-status"><Check size={15} />{events.length} events received{events[0] ? ` · last ${events[0].event_type}` : ''}</div>;
    if (key === 'audio') return <div className="terminal-output">Voice I/O runs on the ULTRON voice layer (STT/TTS).<br/>Press <strong>VOICE MODE</strong> on the core to speak; the transcript appears below the core.</div>;
    return <div className="terminal-output">{toolList.length} capabilities registered. {toolList.slice(0, 6).map(item => item.name).join(' · ') || 'none'}</div>;
  }

  return <main className="ultron-shell">
        <section className="command-header"><div className="command-title"><h2>Command Center</h2><span>YOUR SYSTEM. IN SYNC.</span></div><div className="session-status"><span className={`status-dot ${online ? '' : 'inactive'}`} />{online ? 'SYSTEM OPERATIONAL' : readiness ? 'SYSTEM DEGRADED' : 'SYSTEM STANDBY'}<span> / </span>{principal?.username ?? 'SESSION'} · {clock}</div></section>
    <div className="workspace-grid"><aside className="widget-column" aria-label="Tasks and system access"><NetworkPanel /><TaskPanel /><AgentsPanel /></aside>
    <section className="core-workspace" aria-label="Ultron orchestrator"><div className="core-headline">ORCHESTRATOR <span>/</span> <strong>CORE 01</strong> <span>/</span> {online ? 'ACTIVE' : 'STANDBY'}</div><CoreDisplay online={online} paused={paused} voice={voice} voiceState={voiceState} mode={mode} onToggleVoice={toggleVoice} /><CoreMetrics agentActive={activeAgents} agentTotal={agentList.length} taskOpen={openTasks} latencyMs={latencyMs} />
      {voice && <div className="voice-hud" role="status" aria-live="polite">
        <div className="voice-hud-head"><span className="status-dot" />VOICE · {voiceState.toUpperCase()}</div>
        {voicePartial ? <div className="voice-hud-line user">{voicePartial}</div> : voiceFinal ? <div className="voice-hud-line user">{voiceFinal}</div> : null}
        {voiceAnswer && <div className="voice-hud-line core">{voiceAnswer}</div>}
        {voiceError && <div className="voice-hud-error">{voiceError}</div>}
        <div className="voice-hud-hint">Speak naturally — pause to send. Talk over ULTRON to interrupt.</div>
      </div>}
      <div className="mode-selector" aria-label="Core protocol">{[{name:'Focus',icon:Box},{name:'Code',icon:Code2},{name:'Creative',icon:Layers}].map(({name,icon:Icon}) => <Button key={name} variant="ghost" aria-pressed={mode === name} onClick={() => setMode(name)}><Icon />{name.toUpperCase()}</Button>)}</div><div className="core-footer-label"><ShieldCheck size={10} />PROTECTED BY ULTRON SENTINEL</div></section>
    <aside className="widget-column" aria-label="Session, capabilities and events"><SessionPanel /><ToolsPanel /><EventsPanel /><HealthPanel /></aside></div>
    <CommandConsole />
    <footer className="bottom-bar"><div className="bottom-brand"><Cpu />ULTRON OS <span>LIVE</span></div><nav className="app-dock" aria-label="Applications">{dock.map(({icon:Icon,label})=><Button key={label} variant="ghost" size="icon" title={label} aria-label={`Open ${label}`} onClick={()=>label === 'Command center' ? window.scrollTo({top:0,behavior:'smooth'}) : setTool(label === 'Network' ? 'Network' : label)}><Icon /></Button>)}</nav><div className="bottom-system"><span><Wifi /><b>{streamState === 'open' ? 'LIVE' : '—'}</b></span><span><BatteryFull /><b>{activeAgents}↑</b></span><span><span className={`status-dot${online ? '' : ' inactive'}`} />{online ? 'CONNECTED' : 'STANDBY'}</span></div></footer>
    {tool && <div className="dialog-backdrop" onClick={() => setTool(null)}><div ref={dialogRef} tabIndex={-1} className="tool-dialog" role="dialog" aria-modal="true" aria-labelledby="tool-title" onClick={e=>e.stopPropagation()}><div className="dialog-header"><h2 id="tool-title">{tool}</h2><Button variant="ghost" size="icon" aria-label="Close dialog" onClick={()=>setTool(null)}><X /></Button></div>
      {renderDialog()}
    </div></div>}
  </main>;
}
