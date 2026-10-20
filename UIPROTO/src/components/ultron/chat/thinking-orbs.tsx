/**
 * Thinking orb (ported from the Ultron reference repo's `ui/thinking-orbs.tsx`).
 * Re-exports the `thinking-orbs` canvas component so the chat UI can render the
 * same six animated states (working · searching · solving · listening ·
 * composing · shaping).
 */
import { ThinkingOrb } from "thinking-orbs";
export type { ThinkingOrbProps, OrbState, OrbSize, OrbTheme } from "thinking-orbs";

export { ThinkingOrb };
export default ThinkingOrb;
