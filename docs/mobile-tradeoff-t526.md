# T526: Share-vs-separate Trade-off Decision (Mobile PWA)

**Objective:** Decide reuse vs separate mobile app before restructuring (A§69.8)
**Dependencies:** T523, T524
**Date:** 2026-10-18

## Key Constraint

Mobile never reaches the Windows Node Bridge (A§69.8). All OS actions must remain desktop-only (Electron main process). No IPC surface from mobile to Node bridge.

## Options

### Option 1: Reuse UIPROTO as responsive PWA (Share)
**Approach:** Extend existing TanStack Start SPA for mobile responsiveness. Add PWA manifest, service worker, offline behavior.

**Pros:**
- Maximum code reuse (90%+ of UI, state, API client)
- Single source of truth for UI/UX and contracts
- Shared types via clients/shared/types.ts (T524)
- Faster iteration, consistent branding
- Leverages existing components/design system
- Same routing, auth, query cache patterns
- Easier to maintain (one codebase)

**Cons:**
- Some desktop-specific UI (inspector rail, multi-panel) may be awkward on small screens
- Bundle size larger than minimal mobile app
- May need responsive refactoring in multiple places
- PWA caching complexity

### Option 2: Separate mobile app (Standalone)
**Approach:** Build separate app in clients/mobile/ using React Native/Capacitor or separate Vite PWA.

**Pros:**
- Mobile-optimized UX (bottom nav, gestures)
- Smaller bundle, platform-specific optimizations
- Native capabilities if needed
- Clear separation

**Cons:**
- Significant duplication of logic/UI
- Contract drift risk (mitigated by shared types)
- More maintenance overhead
- UX divergence
- Another build/toolchain

## Recommendation

**REUSE: Make UIPROTO responsive as a PWA (Option 1)**

**Rationale:**
1. A§69.8 states installable config + offline behavior - fits PWA on same codebase
2. T526 says "how much of console is shippable as responsive PWA" - implies adaptation
3. Shared contracts (T523) + shared types (T524) enable clean reuse
4. Node bridge only exists in Electron preload/main; web/PWA build never references it
5. MVP velocity + maintainability outweigh perfect native UX
6. Tailwind utilities available; mobile layouts via breakpoints

## Decision

**Adopt Option 1 (Share/Reuse).** Do not create separate mobile codebase yet. Restructure minimally for responsiveness + add PWA installability. Matches A§69.8. No substantial restructuring before this decision.

## Implementation Notes

- Ensure mobile/PWA never imports Electron-specific APIs
- Guard with 	ypeof window !== 'undefined' && 'ultron' in window checks (Electron only)
- Follow T527 for PWA manifest, SW, offline behavior
- Offline must show honest state, never stale-as-live data (A§69.8)
