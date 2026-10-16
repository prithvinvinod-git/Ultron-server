import { useState } from 'react';
import { ArrowDown, ArrowUp, Check, ChevronRight, CloudSun, FileText, Folder, MoreHorizontal, Pause, Play, Plus, Radio, SkipBack, SkipForward, Sun, Volume2, Wifi, Wind } from 'lucide-react';
import { Button } from '@/components/ui/button';

export function PanelHeading({ label, detail, action }: { label: string; detail?: string; action?: React.ReactNode }) {
  return <div className="panel-heading"><h2>{label}</h2>{detail && <span>{detail}</span>}{action}</div>;
}

export function NetworkPanel() {
  return <section className="hud-panel network-panel"><PanelHeading label="CONNECTION" detail="SECURE" /><div className="network-main"><div className="icon-tile"><Wifi /></div><div><strong>Home Network</strong><p>192.168.1.103 <span className="status-dot" /></p></div><span className="signal-strength">99<small>%</small></span></div><div className="signal-meter"><span /></div><div className="panel-foot"><span>5 GHz · WPA3</span><span className="text-primary">CONNECTED</span></div></section>;
}

export function TaskPanel() {
  const [tasks, setTasks] = useState([{ id: 1, label: 'Backup system files', done: true }, { id: 2, label: 'Run security diagnostics', done: false }, { id: 3, label: 'Optimize core modules', done: false }]);
  const [adding, setAdding] = useState(false); const [draft, setDraft] = useState('');
  const complete = tasks.filter(t => t.done).length;
  return <section className="hud-panel"><PanelHeading label="MISSION QUEUE" detail={`${complete}/${tasks.length}`} /><div className="task-list">{tasks.map(task => <Button key={task.id} variant="ghost" className={`task-row ${task.done ? 'task-done' : ''}`} onClick={() => setTasks(tasks.map(t => t.id === task.id ? { ...t, done: !t.done } : t))} aria-pressed={task.done}><span className="task-check">{task.done && <Check size={11} />}</span><span>{task.label}</span></Button>)}</div>{adding ? <form onSubmit={e => { e.preventDefault(); if (!draft.trim()) return; setTasks([...tasks, { id: Date.now(), label: draft.trim(), done: false }]); setDraft(''); setAdding(false); }} className="task-form"><input aria-label="New task" autoFocus value={draft} onChange={e => setDraft(e.target.value)} placeholder="New mission…" /><Button variant="ghost" size="icon" type="submit" aria-label="Save task"><Check /></Button></form> : <Button variant="ghost" className="add-task" onClick={() => setAdding(true)}><Plus />Add task</Button>}</section>;
}

const quickLinks = ['Documents', 'Workspace', 'System terminal', 'Core settings'];
export function QuickLinks({ onOpen }: { onOpen: (tool: string) => void }) {
  return <section className="hud-panel"><PanelHeading label="QUICK ACCESS" action={<Folder size={13} />} /><div className="quick-links">{quickLinks.map((label,i) => <Button variant="ghost" key={label} onClick={() => onOpen(label)}><span className="quick-index">0{i+1}</span><span>{label}</span><ChevronRight /></Button>)}</div></section>;
}

export function NetworkActivity() {
  return <section className="hud-panel activity-panel"><PanelHeading label="NETWORK ACTIVITY" action={<Radio size={13} />} /><div className="chart-label"><span className="status-dot" /> LIVE TRAFFIC <span>60s</span></div><svg viewBox="0 0 250 76" className="network-chart" aria-label="Simulated network activity"><defs><linearGradient id="network-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" className="chart-stop" stopOpacity=".22" /><stop offset="1" className="chart-stop" stopOpacity="0" /></linearGradient></defs><path d="M0 20H250M0 45H250M0 70H250" className="chart-grid" /><path d="M0 60 8 59 14 54 21 59 28 40 34 47 41 38 47 46 54 23 61 39 67 34 74 49 82 46 90 51 98 37 104 41 110 29 118 34 124 20 132 44 140 46 148 34 155 39 164 29 172 45 180 32 186 36 194 11 201 24 208 28 217 18 225 39 231 35 238 46 244 40 250 44 V76H0Z" fill="url(#network-fill)" /><path d="M0 60 8 59 14 54 21 59 28 40 34 47 41 38 47 46 54 23 61 39 67 34 74 49 82 46 90 51 98 37 104 41 110 29 118 34 124 20 132 44 140 46 148 34 155 39 164 29 172 45 180 32 186 36 194 11 201 24 208 28 217 18 225 39 231 35 238 46 244 40 250 44" className="chart-line" /></svg><div className="traffic-metrics"><span><ArrowDown />24.8<small>Mbps</small></span><span><ArrowUp />8.2<small>Mbps</small></span></div></section>;
}

export function WeatherPanel() {
  return <section className="hud-panel weather-panel"><PanelHeading label="ENVIRONMENT" detail="LOCAL" /><div className="weather-location"><span>Los Angeles, CA</span><span>14:32</span></div><div className="weather-now"><div><strong>24<small>°</small></strong><p>Partly cloudy</p></div><CloudSun className="weather-icon" /></div><div className="weather-details"><span><Wind />12 km/h</span><span>Humidity <b>64%</b></span></div><div className="weather-forecast">{[{ day: 'TODAY', value: '24°', Icon: CloudSun },{ day: 'WED', value: '26°', Icon: Sun },{ day: 'THU', value: '23°', Icon: CloudSun }].map(({day,value,Icon}) => <div key={day}><span>{day}</span><Icon /><strong>{value}</strong></div>)}</div></section>;
}

export function NotesPanel({ onOpen }: { onOpen: (tool: string) => void }) {
  return <section className="hud-panel"><PanelHeading label="FIELD NOTES" action={<Button variant="ghost" size="icon" className="small-icon" onClick={() => onOpen('Field notes')} aria-label="Open field notes"><MoreHorizontal /></Button>} /><div className="notes-list">{['Core architecture v2', 'Project Ultron — roadmap', 'Ideas & observations'].map((note,i) => <Button key={note} variant="ghost" onClick={() => onOpen(note)}><FileText /><span>{note}</span><small>{['12:45','Yesterday','Oct 11'][i]}</small></Button>)}</div><div className="panel-foot"><span>3 notes</span><span>LAST SYNC 2 MIN AGO</span></div></section>;
}

const tracks = [{ title: 'Neon Horizon', artist: 'HOME · Odyssey', duration: '4:21' }, { title: 'Resonance', artist: 'HOME · Odyssey', duration: '3:32' }, { title: 'Nightfall', artist: 'System Mix · Ambient', duration: '5:08' }];
export function MusicPanel() {
  const [playing, setPlaying] = useState(false); const [track, setTrack] = useState(0); const current = tracks[track];
  if (!current) return null;
  return <section className="hud-panel music-panel"><PanelHeading label="NOW PLAYING" detail="AMBIENT MIX" /><div className="track-info"><div className={`album-art ${playing ? 'is-playing' : ''}`}><div className="album-sun" /><div className="album-grid" /><span>HOME</span></div><div><strong>{current.title}</strong><p>{current.artist}</p></div><div className={`equalizer ${playing ? 'playing' : ''}`}>{[1,2,3,4].map(i=><i key={i} />)}</div></div><input className="music-progress" type="range" min="0" max="100" defaultValue="31" aria-label="Track position" /><div className="track-times"><span>1:24</span><span>{current.duration}</span></div><div className="player-controls"><Volume2 size={14} /><div><Button variant="ghost" size="icon" aria-label="Previous track" onClick={() => setTrack((track + tracks.length - 1) % tracks.length)}><SkipBack /></Button><Button variant="ghost" size="icon" className="play-button" aria-label={playing ? 'Pause music' : 'Play music'} onClick={() => setPlaying(!playing)}>{playing ? <Pause /> : <Play />}</Button><Button variant="ghost" size="icon" aria-label="Next track" onClick={() => setTrack((track+1)%tracks.length)}><SkipForward /></Button></div><span className="music-output">SIM</span></div></section>;
}
