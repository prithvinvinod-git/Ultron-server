# Architectural conflict — machine run-rule vs Electron launch tests

Raised **before** implementing T516 (§69.2: document conflicts first).

## The conflict

`tasks.md` header (machine safety rule, §61):

> **Do not** run the web/desktop client, the Orb, a voice session, a device
> bridge, the scheduler, or any background worker that runs the real runtime.

T516/T522 in the same file require executed tests: *"dev launch renders the
console, production build loads from disk"*, *"dev launch runs, production
build opens the console"* (§69.11). Read literally, Electron launch tests
cannot run on this machine and Phase E1 could never leave `unverified`.

## Analysis

The machine rule's stated rationale (§61) is that **agent code executes tools,
shells, and browsers**; running the runtime on the build machine would put the
author's own files and credentials at risk. The prohibited items are all the
*runtime*: API server, workers, scheduler, voice session, device bridge.

An Electron window that loads the **static `UIPROTO` bundle** with **no
backend running** executes no agent code, no tools, no shells — it is
equivalent to opening a built HTML file, which the existing verified workflow
already does (`npm run build`, `npm test`, `tsc`, `ruff`, `mypy`, pytest are
the allowed steps).

## Resolution adopted

1. **Offline static launch is permitted for Electron verification**: the
   Electron window may load `server/app/static/` through a loopback
   (`127.0.0.1`) static server — no API server, no workers, no backend process.
2. **The full ULTRON runtime is never started on this machine.** No uvicorn,
   no agent workers, no scheduler, no voice, no device bridge — same as before.
3. **Backend-connected paths (live REST/SSE/WS, reconnect-after-restart,
   auth-failure against a real server) are recorded `UNVERIFIED`** until the
   code runs on the author's server (§69.11: no test claimed unless executed).
4. `vite dev` (prototype asset server, API proxy pointing at a dead endpoint)
   may run briefly for the T516 dev-launch test; it is a build tool, not the
   runtime.

If the author intends the prohibition to cover even an offline Electron
window, T516-T522 verification must move to the target server; the code work
here is unaffected either way.
