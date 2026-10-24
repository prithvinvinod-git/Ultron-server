# AGENTS.md

Working agreement for coding agents (opencode, Claude Code, ...) working in this
repository. The domain documents under `docs/` describe ULTRON's subsystems and
are *not* instructions for agents; `docs/agents.md` in particular is about
ULTRON's own `Agent` runtime.

## Layout

| Path | What lives there |
|---|---|
| `server/app/` | The Python (FastAPI) core: `config/`, `voice/`, `api/routes/`, `agents/`, `core/` |
| `server/tests/` | Pytest suites (`unit/`, `integration/`) |
| `UIPROTO/` | The web console (Vite + React + TanStack). `UIPROTO/AGENTS.md` holds the Lovable-specific rules |
| `clients/electron/` | The Electron shell that wraps the same console |
| `scripts/` | Windows/POSIX helper scripts (`dev`, `start`, `test`, `lint`, `setup`) |
| `runapp.md` | How to run ULTRON locally (env precedence, ports, provider setup) |

## Commands

```powershell
# backend
cd server
.venv\Scripts\python.exe -m pytest tests/unit
.venv\Scripts\python.exe -m ruff check app tests
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# web console
cd UIPROTO
npm run build
npx tsc --noEmit -p tsconfig.json
npm test
```

`npm run lint` in `UIPROTO` is currently **not** a usable gate: the working tree
uses CRLF line endings and 4-space indentation while the Prettier config expects
LF and 2 spaces, so untouched files fail the same way. Use `tsc` and `vitest`
instead, and do not reformat unrelated files to "fix" lint.

## Working rules

1. **Keep the todo list updated as you go.** Create the list before the work
   starts, mark the item in progress while it is being done, and mark it
   completed only after it is verified. Add a new item the moment a new problem
   surfaces -- do not silently absorb extra work into the current item.
2. Never revert or reformat work that the user made in the working tree. The
   tree contains unrelated in-progress changes; touch only what the task needs.
3. Report pre-existing failures as pre-existing. Do not "fix" an unrelated test
   or lint error just to make a run green.
4. Provider keys live in the gitignored `.env` files (and, on this machine, in
   the Windows user environment). Never write a secret into a tracked file.
5. Configuration precedence: the process environment beats `.env`, and the root
   `.env` beats `server/.env`. See `runapp.md` before changing how a server is
   launched.
