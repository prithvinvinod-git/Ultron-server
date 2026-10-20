/**
 * Suggestion cards shown on an empty transcript (ported from the Ultron
 * reference repo's `chat/suggestion-cards.tsx`, adapted to ULTRON tokens).
 */
"use client";

import { Calculator, Clock3, Globe2, BrainCircuit } from "lucide-react";

const SUGGESTIONS = [
  {
    icon: BrainCircuit,
    title: "What do you know?",
    subtitle: "Recall my long-term memory",
    prompt: "What do you currently know about me from memory?",
  },
  { icon: Clock3, title: "Time check", subtitle: "Uses the current-time tool", prompt: "What time is it right now?" },
  { icon: Calculator, title: "Math", subtitle: "Safely evaluate: 1984 × 0.375 ÷ 2", prompt: "What is 1984 × 0.375 ÷ 2?" },
  {
    icon: Globe2,
    title: "Web search",
    subtitle: "Find something up to date",
    prompt: "Search the web for the latest on SpaceX Starship.",
  },
];

export function SuggestionCards({ onPick, visible }: { onPick: (prompt: string) => void; visible: boolean }) {
  if (!visible) return null;
  return (
    <div className="grid w-full grid-cols-1 gap-2 sm:grid-cols-2">
      {SUGGESTIONS.map(({ icon: Icon, title, subtitle, prompt }) => (
        <button
          key={title}
          onClick={() => onPick(prompt)}
          className="chat-suggestion group flex items-start gap-3 rounded-2xl p-4 text-left transition"
        >
          <Icon size={18} className="mt-0.5 shrink-0 text-primary transition" />
          <span className="min-w-0">
            <span className="block text-sm font-medium text-foreground">{title}</span>
            <span className="block text-xs text-muted-foreground">{subtitle}</span>
          </span>
        </button>
      ))}
    </div>
  );
}
