/**
 * Live-mode activity mapping and barge-in helpers (ported from the Ultron
 * reference repo's `lib/activity.ts`, adapted to ULTRON). ULTRON's own
 * `VoiceClient` already runs the server-side STT/TTS and barge-in; these helpers
 * only shape the *labels* the live view shows and recognises a stop phrase.
 */

export type ActivityKind =
    "thinking" | "searching" | "reading" | "generating" | "calculating" | "remembering" | "working";

/** Maps a tool the agent invoked onto the coarse activity the user is told about. */
export const ACTIVITY_BY_TOOL: Record<string, ActivityKind> = {
    search_web: "searching",
    open_url: "reading",
    calculate: "calculating",
    recall_memories: "remembering",
    store_memory: "remembering",
    get_time: "working",
    system_info: "working",
};

export function activityForTool(name: string): ActivityKind {
    return ACTIVITY_BY_TOOL[name] ?? "working";
}

export function activityLabel(activity: ActivityKind): string {
    switch (activity) {
        case "searching":
            return "Searching the web…";
        case "reading":
            return "Reading the page…";
        case "calculating":
            return "Crunching the numbers…";
        case "remembering":
            return "Checking my memory…";
        case "generating":
            return "Generating…";
        case "working":
            return "Working on it…";
        default:
            return "Thinking…";
    }
}

const STOP_PHRASES = [
    "stop",
    "stop that",
    "stop talking",
    "stop reading",
    "cancel",
    "never mind",
    "nevermind",
    "shut up",
    "quiet",
    "silence",
    "that's enough",
    "thats enough",
    "enough",
];

export function isStopPhrase(text: string): boolean {
    const clean = text
        .toLowerCase()
        .replace(/[^a-z'\s]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    if (!clean) return false;
    if (STOP_PHRASES.includes(clean)) return true;
    const words = clean.split(" ");
    return words.length <= 3 && STOP_PHRASES.some((p) => p.includes(clean) || clean.includes(p));
}
