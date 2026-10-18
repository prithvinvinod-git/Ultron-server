# T515 — Prototype Preservation Baseline

Gate for `tasks.md` **T515** (§69.3). Records the non-destructive recovery
point before any Electron integration change.

## Recovery point

| Item | Value |
|---|---|
| Integration branch | `feature/unified-clients` (created at `82b5dac`) |
| Baseline commit | `82b5dac` — *T514: client architecture audit + implementation report* |
| Prior branch (untouched) | `feature/phase-1-foundation` at the same commit |
| Working branch policy | all E1 (T516–T522) work happens on `feature/unified-clients` |

`UIPROTO/` is byte-for-byte identical at `82b5dac` to its state at the end of
T513 (the last UI-touching commit, `a4a10f1`); T514 only added
`docs/uiclient-audit-t514.md` and the `tasks.md` ledger line.

## Lovable constraints (`UIPROTO/AGENTS.md`)

- The branch is connected to Lovable; **never rewrite pushed history** (no
  force-push, rebase, amend, or squash of pushed commits).
- Keep the branch in a working state; commits sync back to the Lovable editor.
- Consequence for E1: Electron work lives in `clients/electron/` (outside
  `UIPROTO/`), and any future touch inside `UIPROTO/` is a normal forward
  commit with a documented reason (§69.3).

## Inventory — preserved assets (visual source of truth)

SHA-256 manifest of all 98 tracked source files under `UIPROTO/` (excluding
`node_modules/` and `dist/`) recorded at baseline:

```
Get-ChildItem -Recurse UIPROTO -File |
  Where-Object { $_.FullName -notmatch 'node_modules|dist' } |
  Get-FileHash -Algorithm SHA256
```

(groups: 2 lockfiles, configs, 4 route files, ~15 Ultron components,
~12 lib modules, styles, assets, tests)

### Layouts (must not change)

| Surface | File | Notes |
|---|---|---|
| App shell (rail + tabs + inspector + console) | `src/components/ultron/shell/app-shell.tsx` | fixed non-scrolling viewport |
| Command tab (3-column grid + core) | `src/routes/index.tsx` | `workspace-grid` / `core-workspace` |
| Inspector slide-over | `shell/inspector.tsx` | selection-driven |
| Login / change password | `login.tsx`, `shell/change-password.tsx` | |

### Component hierarchy (must not change)

`AppShell` → tab view (`PanelGrid` or command view) → panels
(`hud-panel` family) → `CommandConsole`; inspector overlays the same shell.

### Design tokens (must not change)

`src/styles.css` — semantic tokens (`--hud-*`), `hud-panel`, `panel-heading`,
`status-dot`, `shell-form`, workspace grid, voice HUD. Colors, gradients,
typography, spacing, icons (lucide), animations all originate here.

### Assets (must not change)

`src/assets/` (logo PNG), `favicon.png`, `robots.txt`; built copies live in
`server/app/static/`.

### State / transport (must not change without a documented reason)

`src/lib/api.ts` (REST + silent refresh), `src/lib/events.ts` (single SSE
store), `src/lib/queries.ts`, `src/lib/auth.tsx`, `src/lib/workspace.tsx`.

## Baseline verification (executed)

```
git branch feature/unified-clients          # created at 82b5dac
git checkout feature/unified-clients
cd UIPROTO && npm run build
  [prerender] Prerendered 1 pages: /          (build succeeded)
.\node_modules\.bin\tsc --noEmit
  TSC:0                                        (clean)
npm test
  2 files / 6 tests passed                     (unchanged from T513)
```

`UIPROTO` is unchanged on the baseline: no source edit was made by T514/T515.

## Next task

**T516** — Electron main process loading the existing UI from
`clients/electron/`, with `UIPROTO/` untouched.
