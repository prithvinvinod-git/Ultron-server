import { useEffect, useState } from 'react';
import { Bot } from 'lucide-react';

const base = [
  { name: 'SENTINEL', task: 'Monitoring security perimeter', p: 72, on: true },
  { name: 'VISION', task: 'Indexing workspace files', p: 41, on: true },
  { name: 'JARVIS-X', task: 'Drafting daily briefing', p: 88, on: true },
  { name: 'ECHO', task: 'Idle · awaiting command', p: 0, on: false },
];

export function AgentsPanel() {
  const [agents, setAgents] = useState(base);
  useEffect(() => { const t = setInterval(() => setAgents(a => a.map(x => x.on ? { ...x, p: x.p >= 100 ? 8 : x.p + Math.ceil(Math.random() * 4) } : x)), 1500); return () => clearInterval(t); }, []);
  const active = agents.filter(a => a.on).length;
  return <section className="hud-panel">
    <div className="panel-heading"><h2>AGENTS · {active}/{agents.length} ACTIVE</h2><Bot size={13} /></div>
    <div className="agent-list">{agents.map(a => <div key={a.name} className="agent-row">
      <span className="agent-dot" data-on={a.on} />
      <div><strong>{a.name}</strong><small>{a.task}</small></div>
      <span className="agent-pct">{a.on ? `${Math.min(a.p, 100)}%` : '—'}</span>
      <div className="agent-bar"><span style={{ width: `${Math.min(a.p, 100)}%` }} /></div>
    </div>)}</div>
  </section>;
}
