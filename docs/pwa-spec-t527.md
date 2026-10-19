# T527: PWA Installable Config + Offline Behavior

**Objective:** Installability and graceful offline (A§69.8)
**Dependencies:** T526
**Date:** 2026-10-18

## Requirements

1. Web app manifest (done partially - added to UIPROTO/public/manifest.json)
2. Service worker with appropriate caching (done partially - sw.js added)
3. App-shell caching, offline fallback states
4. Cache-invalidation rules
5. Install prompt available
6. App loads offline to a stated state (honest state, never stale-as-live data)

## Implementation

- Link manifest in HTML shell (_shell.html): <link rel="manifest" href="/manifest.json">
- Register SW (done via PWARegister component - need to mount it in root)
- Update SW for offline fallback (e.g., serve index.html for navigation requests when offline)
- Handle offline state in UI (show banner/indicator)

## Mount PWARegister

Add PWARegister to root layout.
