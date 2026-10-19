# T528: PWA auth + core screens

The mobile surface: the existing console made usable on a phone-sized viewport,
reusing the design tokens, with every operation routed through Core.

## Scope

The console is shared as a responsive PWA (decision T526); mobile never reaches
the Windows Node Bridge. T528 makes the core screens responsive and records the
auth decision.

## Responsive layout

The unified shell (`ultron-shell` → `command-header` / `shell-body` /
`bottom-bar`) previously had a desktop-only layout (rules gated at
`min-width:901px`). T528 adds a mobile block in `UIPROTO/src/styles.css`:

- **Tab rail** becomes a horizontal, sticky, scrollable bar at the top instead of
  a vertical left column.
- **Workspace** stacks to a single column; the command-center `workspace-grid`
  collapses to one column (core first, then panels).
- **Inspector** moves below the workspace, full width, scroll-capped.
- **Panels** go single-column; `panel-wide` no longer spans two columns.
- **Header/footer** wrap instead of overflowing.
- A `max-width:520px` block tightens spacing for small phones.

All colours, fonts and shapes come from the existing semantic tokens in
`styles.css`; no new design system was introduced.

## Auth

The console **bypasses the authentication gate** by directive: `AuthGate` in
`src/routes/__root.tsx` returns `<Outlet />` unconditionally, the shell shows a
static principal, and `ChangePasswordDialog` is stubbed.

The `LoginScreen` component (`src/components/ultron/login.tsx`) and the
`AuthProvider` (`src/lib/auth.tsx`) remain in the tree, fully implemented against
the real `/auth/login`, `/auth/me`, `/auth/refresh`, `/auth/logout` contracts, but
are **not wired** into the render path. Re-enabling the gate is a one-line change
in `AuthGate`; it is intentionally disabled, not deleted.

Because the gate is bypassed, the client makes no auth call. Every *data*
operation still flows through Core, so no operation bypasses Core authorization
once the server enforces it — the bypass is a UI convenience, not a second code
path to the backend.

## Core screens (reused, responsive)

| Screen | Component | Notes |
|---|---|---|
| Chat / conversation | `CommandConsole` | Routed through Core chat. |
| Agent status | `AgentsPanel`, `PanelGrid tab="agents"` | Live via queries + event stream. |
| Task status | `TaskPanel`, `PanelGrid tab="tasks"` | Live via queries. |
| Remote operations | `PanelGrid tab="fleet"`, inspector controls | Node/device operations via Core. |
| System/health | `HealthPanel`, `SystemPanel` | `/health`, `/ready`. |

## Verification

- `UIPROTO`: `.\node_modules\.bin\tsc --noEmit` → exit 0.
- Responsive rendering at phone width is **unverified in this session** (no
  browser was driven); the CSS media queries are static and type-checked, not
  visually confirmed.

## Deferred

- Live install-prompt and offline-load behaviour (T527) is unverified on a live
  browser.
- Re-enabling the auth gate reverses the current bypass and is deferred.
