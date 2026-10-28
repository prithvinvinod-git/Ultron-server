import { createFileRoute } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Box, Code2, Layers, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { AgentsPanel } from '@/components/ultron/agents';
import { CommandConsole } from '@/components/ultron/command-console';
import { CoreDisplay, CoreMetrics } from '@/components/ultron/core-display';
import { AppShell } from '@/components/ultron/shell/app-shell';
import { EventsPanel, HealthPanel, NetworkPanel, SessionPanel, TaskPanel, ToolsPanel } from '@/components/ultron/widgets';
import { PanelGrid } from '@/components/ultron/shell/registry';
import { SchedulesPanel } from '@/components/ultron/schedules';
import { SettingsPanel } from '@/components/ultron/settings';
import { useEventStream } from '@/lib/events';
import { voiceApi } from '@/lib/api';
import { getVoicePreferences } from '@/lib/voice-prefs';
import { ChatPanel } from '@/components/ultron/chat/chat-panel';
import { ChatWorkspace } from '@/components/ultron/chat/chat-workspace';
import { useLiveSession } from '@/components/ultron/chat/use-live-session';
import { useAgents, useHealth, useLiveInvalidation, useTasks } from '@/lib/queries';
import { useWorkspace } from '@/lib/workspace';
import type { TabId } from '@/lib/workspace';
import { MemoryPanel } from '@/components/ultron/memory-panel';

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

// Preset greetings played on voice mode entry
const GREETINGS = [
  'Hi sir, Ultron here.',
  'Good morning sir, how is everything going?',
  'Ultron online. How can I help you?',
  'Welcome back. All systems nominal.',
];

function PlannedPanel({ title, task }: { title: string; task: string }) {
  return (
    <section className="hud-panel">
      <div className="panel-heading"><h2>{title}</h2><span>PLANNED</span></div>
      <div className="panel-planned">This surface is wired to the core in {task}.</div>
    </section>
  );
}

function Index() {
  useLiveInvalidation();
  const { tab } = useWorkspace();
  const { state: streamState } = useEventStream();
  const health = useHealth();
  const agents = useAgents();
  const tasks = useTasks();

  const [paused, setPaused] = useState(false); const [mode, setMode] = useState('Focus');
  const [voiceError, setVoiceError] = useState<string | null>(null);

  const [chatOpen, setChatOpen] = useState(false);
  const [chatSeed, setChatSeed] = useState<{ id: number; prompt: string } | null>(null);
  const seedCounter = useRef(0);
  function openChat(prompt: string) {
    setChatSeed({ id: ++seedCounter.current, prompt });
    setChatOpen(true);
  }

  const live = useLiveSession({ onError: setVoiceError });
  const voice = live.active || live.status !== 'idle';
  const voiceState = live.status;

  // Orb → console voice gestures. The orb's start/stop buttons reach the main
  // process, which forwards `voice:request` to this renderer through the narrow
  // `window.ultron.onVoiceRequest` bridge. The orb does not own a voice
  // pipeline — this listener drives the same `useLiveSession` the console uses.
  const liveRef = useRef(live);
  liveRef.current = live;
  const toggleVoiceRef = useRef(toggleVoice);
  toggleVoiceRef.current = toggleVoice;
  useEffect(() => {
    type VoiceRequestBridge = {
      onVoiceRequest?: (
        listener: (request: { action?: string; source?: string }) => void,
      ) => () => void;
    };
    const bridge = (window as unknown as { ultron?: VoiceRequestBridge }).ultron;
    const off = bridge?.onVoiceRequest?.((request) => {
      if (request.action === 'start') {
        if (!liveRef.current.active) void toggleVoiceRef.current();
      } else if (request.action === 'stop' || request.action === 'cancel') {
        if (liveRef.current.active) liveRef.current.stop();
      }
    });
    return () => off?.();
  }, []);

  async function toggleVoice() {
    if (live.active) { live.stop(); return; }
    setVoiceError(null);
    try {
      const prefs = getVoicePreferences();
      if (!prefs.enabled) {
        setVoiceError('Voice is turned off in Settings.');
        return;
      }
      // The browser STT engine runs locally, so it does not need the server to
      // have a speech engine configured.
      if (prefs.stt !== 'browser') {
        const engines = await voiceApi.engines();
        if (!engines.enabled || !engines.stt.available) {
          setVoiceError(engines.stt.reason ?? (engines.enabled ? 'Speech-to-text is unavailable on the server.' : 'Voice is disabled on the server.'));
          return;
        }
      }
      await live.start();
      // Speak a preset greeting directly through TTS so ULTRON greets instantly,
      // without spending a model turn on "hello".
      const greeting = GREETINGS[Math.floor(Math.random() * GREETINGS.length)] ?? GREETINGS[0]!;
      void live.greet(greeting);
    } catch (error) {
      setVoiceError(error instanceof Error ? error.message : 'Could not start voice mode.');
    }
  }

  const agentList = agents.data ?? [];
  const taskList = tasks.data ?? [];
  const readiness = health.data?.readiness;
  const online = readiness?.ready ?? streamState === 'open';
  const activeAgents = agentList.filter(agent => ACTIVE_AGENT_STATUSES.has(agent.status)).length;
  const openTasks = taskList.filter(task => OPEN_TASK_STATUSES.has(task.status)).length;
  const latencyMs = readiness?.duration_ms ?? 0;

  const views: Record<TabId, ReactNode> = {
    command: <div className="workspace-grid"><aside className="widget-column" aria-label="Tasks and system access"><NetworkPanel /><TaskPanel /><AgentsPanel /></aside>
      <section className={cn('core-workspace', live.active && 'has-voice')} aria-label="Ultron orchestrator"><div className="core-headline">ORCHESTRATOR <span>/</span> <strong>CORE 01</strong> <span>/</span> {online ? 'ACTIVE' : 'STANDBY'}</div><CoreDisplay online={online} paused={paused} voice={voice} voiceState={voiceState} mode={mode} onToggleVoice={toggleVoice} {...(live.active ? { voiceInline: { status: live.status, sttPartial: live.subtitle, captions: live.captions, level: live.level } } : {})} /><CoreMetrics agentActive={activeAgents} agentTotal={agentList.length} taskOpen={openTasks} latencyMs={latencyMs} />
        {voiceError && <div className="voice-hud-error core-voice-error" role="alert">{voiceError}</div>}
        <div className="mode-selector" aria-label="Core protocol">{[{name:'Focus',icon:Box},{name:'Code',icon:Code2},{name:'Creative',icon:Layers}].map(({name,icon:Icon}) => <Button key={name} variant="ghost" aria-pressed={mode === name} onClick={() => setMode(name)}><Icon />{name.toUpperCase()}</Button>)}</div><div className="core-footer-label"><ShieldCheck size={10} />PROTECTED BY ULTRON SENTINEL</div></section>
      <aside className="widget-column" aria-label="Session, capabilities and events"><SessionPanel /><ToolsPanel /><EventsPanel /><HealthPanel /></aside></div>,
    chat: <ChatWorkspace onStartLive={() => { void toggleVoice(); }} liveActive={live.active} />,
    agents: <PanelGrid tab="agents" />,
    tasks: <PanelGrid tab="tasks" />,
    fleet: <PanelGrid tab="fleet" />,
    schedules: <div className="shell-panels"><SchedulesPanel /></div>,
    notifications: <PanelGrid tab="notifications" />,
    conversations: <PanelGrid tab="conversations" />,
    models: <PanelGrid tab="models" />,
    system: <PanelGrid tab="system" />,
    settings: <SettingsPanel />,
    memory: <MemoryPanel />,
  };

  return <AppShell>{views[tab]}{!live.active && <CommandConsole onChat={openChat} />}<ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} seed={chatSeed} onStartLive={() => { setChatOpen(false); void toggleVoice(); }} /></AppShell>;
}
