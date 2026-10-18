import { createFileRoute } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Box, Code2, Layers, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { AgentsPanel } from '@/components/ultron/agents';
import { CommandConsole } from '@/components/ultron/command-console';
import { CoreDisplay, CoreMetrics } from '@/components/ultron/core-display';
import { AppShell } from '@/components/ultron/shell/app-shell';
import { EventsPanel, HealthPanel, NetworkPanel, SessionPanel, TaskPanel, ToolsPanel } from '@/components/ultron/widgets';
import { PanelGrid } from '@/components/ultron/shell/registry';
import { SchedulesPanel } from '@/components/ultron/schedules';
import { useEventStream } from '@/lib/events';
import { voiceApi } from '@/lib/api';
import { VoiceClient } from '@/lib/voice';
import type { VoiceState } from '@/lib/voice';
import { useAgents, useHealth, useLiveInvalidation, useTasks } from '@/lib/queries';
import { useWorkspace } from '@/lib/workspace';
import type { TabId } from '@/lib/workspace';

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
  const [voice, setVoice] = useState(false);
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [voicePartial, setVoicePartial] = useState(''); const [voiceFinal, setVoiceFinal] = useState('');
  const [voiceAnswer, setVoiceAnswer] = useState(''); const [voiceError, setVoiceError] = useState<string | null>(null);
  const voiceRef = useRef<VoiceClient | null>(null);

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

  const agentList = agents.data ?? [];
  const taskList = tasks.data ?? [];
  const readiness = health.data?.readiness;
  const online = readiness?.ready ?? streamState === 'open';
  const activeAgents = agentList.filter(agent => ACTIVE_AGENT_STATUSES.has(agent.status)).length;
  const openTasks = taskList.filter(task => OPEN_TASK_STATUSES.has(task.status)).length;
  const latencyMs = readiness?.duration_ms ?? 0;

  const views: Record<TabId, ReactNode> = {
    command: <div className="workspace-grid"><aside className="widget-column" aria-label="Tasks and system access"><NetworkPanel /><TaskPanel /><AgentsPanel /></aside>
      <section className="core-workspace" aria-label="Ultron orchestrator"><div className="core-headline">ORCHESTRATOR <span>/</span> <strong>CORE 01</strong> <span>/</span> {online ? 'ACTIVE' : 'STANDBY'}</div><CoreDisplay online={online} paused={paused} voice={voice} voiceState={voiceState} mode={mode} onToggleVoice={toggleVoice} /><CoreMetrics agentActive={activeAgents} agentTotal={agentList.length} taskOpen={openTasks} latencyMs={latencyMs} />
        {voice && <div className="voice-hud" role="status" aria-live="polite">
          <div className="voice-hud-head"><span className="status-dot" />VOICE · {voiceState.toUpperCase()}</div>
          {voicePartial ? <div className="voice-hud-line user">{voicePartial}</div> : voiceFinal ? <div className="voice-hud-line user">{voiceFinal}</div> : null}
          {voiceAnswer && <div className="voice-hud-line core">{voiceAnswer}</div>}
          {voiceError && <div className="voice-hud-error">{voiceError}</div>}
          <div className="voice-hud-hint">Speak naturally — pause to send. Talk over ULTRON to interrupt.</div>
        </div>}
        <div className="mode-selector" aria-label="Core protocol">{[{name:'Focus',icon:Box},{name:'Code',icon:Code2},{name:'Creative',icon:Layers}].map(({name,icon:Icon}) => <Button key={name} variant="ghost" aria-pressed={mode === name} onClick={() => setMode(name)}><Icon />{name.toUpperCase()}</Button>)}</div><div className="core-footer-label"><ShieldCheck size={10} />PROTECTED BY ULTRON SENTINEL</div></section>
      <aside className="widget-column" aria-label="Session, capabilities and events"><SessionPanel /><ToolsPanel /><EventsPanel /><HealthPanel /></aside></div>,
    agents: <PanelGrid tab="agents" />,
    tasks: <PanelGrid tab="tasks" />,
    fleet: <PanelGrid tab="fleet" />,
    schedules: <div className="shell-panels"><SchedulesPanel /></div>,
    notifications: <div className="shell-panels"><PlannedPanel title="Notifications" task="T512" /></div>,
    system: <PanelGrid tab="system" />,
  };

  return <AppShell>{views[tab]}<CommandConsole /></AppShell>;
}
