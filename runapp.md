# runapp.md - Running ULTRON locally (Windows)

Everything below was verified on this machine (Windows 11, PowerShell 5.1, `uv 0.12.21`,
Node `v22.15.1`, npm `11.17.0`).

---

## Final run sequence (one command) - use this

This is the supported way to start the whole stack. It replaces the ad-hoc per-session commands
and has been verified end-to-end on this machine.

```powershell
.\scripts\ultron.cmd
```

The equivalent PowerShell form (this is what the shortcut runs):

```powershell
.\scripts\ultron.ps1
```

It is idempotent - run it as often as you like; healthy pieces are reused, nothing is duplicated.
In order it:

1. **Docker** - ensures Docker Desktop is up, then starts the `ultron-postgres` container
   (`postgres:17`, volume `ultron_pgdata`, port 5432). The host `postgresql-x64-17` service is
   *not* used: its backends crash with `0xC0000142` on this machine.
2. **Redis** - ensures the `Redis` Windows service is running.
3. **Schema** - runs `alembic upgrade head` every start (a sub-second no-op when current). The
   server never creates tables itself, so skipping this is what produced `500 relation "agents"
   does not exist` after a fresh container.
4. **Environment** - pins `DATABASE_URL` to the root `.env` value and sets
   `REDIS_URL=redis://127.0.0.1:6379/0?protocol=2` (Windows Redis 3.0 is RESP2 only), plus
   `VOICE_ENABLED=true`, `STT_ENGINE=faster-whisper`, `STT_FALLBACK_ENGINE=groq`,
   `TTS_ENGINE=fish-audio`, `TTS_FALLBACK_ENGINE=edge-tts`. Ambient values from an old `setx`,
   an IDE or a parent shell are the usual reason a start "ignored the settings", so the launcher
   always writes them for its own process.
5. **API** - starts uvicorn on `127.0.0.1:8000` (hidden window) and waits for `/health` for up to
   90 s. If the API does not come up it prints the tail of the server error log and does **not**
   open Electron.
6. **Electron** - rebuilds `clients/electron` when its sources are newer than `dist/`, then opens
   the desktop window pointed at the published bundle in `server/app/static`.

Other verbs:

```powershell
.\scripts\ultron.cmd -Status            # what is up, what is down
.\scripts\ultron.cmd -NoElectron        # API + dependencies only
.\scripts\ultron.cmd -Stop              # Electron, API and the Postgres container
.\scripts\ultron.cmd -FixEnv            # clear stale user-level DATABASE_URL/REDIS_URL/voice vars
.\scripts\ultron.cmd -InstallShortcut   # put "ULTRON.lnk" on the desktop (one double-click)
```

Logs: `%TEMP%\ultron\` (`server-out.log`, `server-err.log`, `alembic.log`, `electron-*.log`).
A healthy run prints `+ API status: degraded` only because of the `MODEL_FALLBACK_CHAIN`
advisory; that is not an error. **Do not start another uvicorn on port 8000** - the launcher
reuses the one it finds.

---

## 0. Prerequisites

| Requirement | Notes |
| --- | --- |
| Python 3.12 | Installed through `uv` (`.venv` lives in `server/.venv`) |
| `uv` | `winget install astral-sh.uv` |
| Node 20+ / npm | `node -v`, `npm -v` |
| Redis on `127.0.0.1:6379` | Windows service named `Redis` (already running here) |
| Docker Desktop | Runs PostgreSQL 17 as the `ultron-postgres` container (the host service is broken here) |

---

## 1. One-time setup

### 1.1 Python environment

```powershell
cd server
uv venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev,voice]"
```

or use the repo script (creates the venv and installs the extras):

```powershell
.\scripts\setup.ps1 -Extras dev,voice
```

`voice` pulls in `faster-whisper` and `edge-tts` (the local STT engine and the free TTS
fallback). The cloud engines (Groq STT, Fish Audio / ElevenLabs TTS) speak plain HTTP through
`httpx` and need no extra package - only an API key.

### 1.2 Web UI dependencies

```powershell
cd UIPROTO
npm install
```

### 1.3 Electron shell dependencies

```powershell
cd clients\electron
npm install
```

### 1.4 Database schema

```powershell
cd server
.venv\Scripts\python.exe -m alembic upgrade head
```

The dev database `server/ultron.db` is already migrated; a fresh clone needs the command above.

---

## 2. Configuration

ULTRON reads its configuration from environment variables, optionally seeded from `.env` files.

### 2.1 Which `.env` wins (important)

`server/app/config/settings.py` uses `env_file=(".env", "../.env", "../../.env")`, i.e. when
run from `server/` it reads:

1. `server/.env`
2. `<repo>/.env`  <- **the root file wins** (later entries override earlier ones)
3. `<parent-of-repo>/.env`

Rules of thumb:

- The **process environment beats every `.env` file**.
- `ULTRON_ENV_FILE` replaces the whole list with a single file (use it to pick a config
  explicitly instead of editing the repo files).
- The root `.env` in this repo is the effective one for local runs.

### 2.2 Voice settings (root `.env`)

```dotenv
VOICE_ENABLED=true
STT_ENGINE=faster-whisper
STT_FALLBACK_ENGINE=groq
STT_MODEL=base
TTS_ENGINE=fish-audio
TTS_FALLBACK_ENGINE=edge-tts
```

The Fish Audio key and the cloned-voice `reference_id` live in the **root `.env`** (gitignored):
`FISH_AUDIO_API_KEY`, `FISH_AUDIO_VOICE_ID`, `FISH_AUDIO_MODEL_ID`, `FISH_AUDIO_OUTPUT_FORMAT`.
Do not copy the key into this file or any tracked file.

`server/.env` mirrors the same voice settings (without the Fish Audio key) so that starting
the server from `server/` behaves the same way.

### 2.3 Provider keys that are optional

| Variable | Effect when missing |
| --- | --- |
| `GROQ_API_KEY` | Groq STT reports "not configured"; the server keeps the other STT engines |
| `ELEVENLABS_API_KEY` | ElevenLabs TTS reports "not configured"; Edge TTS keeps working |
| `FISH_AUDIO_API_KEY` | Fish Audio reports "not configured"; the TTS selection falls back to Edge TTS |

---

## 3. Run the backend

### 3.1 Laptop mode (SQLite + Redis) - manual fallback

> The launcher above is the supported path; this section is only for debugging when you want to
> run one piece by hand.

The root `.env` points at PostgreSQL, whose service is stopped on this machine. Override the
database and Redis from the **process environment** so the tracked `.env` files stay untouched:

```powershell
cd server
$env:DATABASE_URL = "sqlite+aiosqlite:///./ultron.db"
$env:REDIS_URL   = "redis://127.0.0.1:6379/0?protocol=2"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`?protocol=2` is required: the Windows Redis 3.0 build does not understand `HELLO` (RESP3)
and answers `unknown command`.

### 3.2 PostgreSQL mode (server host)

Start the `postgresql-x64-17` service, create the `ultron` role/database, and run:

```powershell
cd server
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3.3 Repo scripts

```powershell
.\scripts\dev.ps1      # auto-reload
.\scripts\start.ps1    # no reload
.\scripts\test.ps1     # unit + integration tests
```

Both scripts run the API from `server/` and honour the same env precedence.

### 3.4 Useful URLs

| URL | Purpose |
| --- | --- |
| `http://127.0.0.1:8000/health` | liveness + dependency probes |
| `http://127.0.0.1:8000/ready` | readiness |
| `http://127.0.0.1:8000/voice/options` | available STT/TTS providers and their status |
| `http://127.0.0.1:8000/docs` | OpenAPI UI |
| `ws://127.0.0.1:8000/voice` | voice WebSocket (config + text turns) |
| `http://127.0.0.1:8000/` | the bundled web UI (if `server/app/static` was published) |

---

## 4. Run the web UI

### 4.1 Bundled UI (served by FastAPI)

The published bundle lives in `server/app/static/`. When it is present, FastAPI serves the SPA
at `/`, so only the backend needs to run.

To rebuild it:

```powershell
cd UIPROTO
npm run build
```

### 4.2 Vite dev server (hot reload)

```powershell
cd UIPROTO
npm run dev
```

- Dev server: <http://localhost:8080> (port 8080 is fixed by the Lovable TanStack config).
- `UIPROTO/vite.config.ts` proxies every API prefix (`/voice`, `/auth`, `/chat`, `/events`, ...)
  to `http://127.0.0.1:8000`, so the backend must be running on port 8000.

---

## 5. Run the Electron shell

The Electron app wraps the same UIPROTO console; it never re-implements it. In normal use the
launcher (top of this file) starts it for you.

```powershell
cd clients\electron
npm start            # builds TypeScript, serves server/app/static, opens the window
```

- `ULTRON_BACKEND_URL` selects the backend (default `http://127.0.0.1:8000`).
- `npm run smoke` builds and runs a headless render check (`electron . --smoke`).
- `npm run start:dev` / `npm run dev:app` point the window at the Vite dev server
  (`ULTRON_DEV_SERVER_URL=http://localhost:8080`), so run `npm run dev` in `UIPROTO` first.

The Electron static server forwards `/voice` (WebSocket) and `/nodes` to the backend, so voice
works identically in the desktop shell.

---

## 6. Voice subsystem

### 6.1 Provider selection

Selection happens in the UI: **Settings -> Voice**. The browser stores only the chosen
provider names in `localStorage`; provider keys stay on the server.

| Kind | Providers |
| --- | --- |
| STT | `faster-whisper` (local, **the client default**), `groq` (needs a key), `browser` (Web Speech API, Chrome/Edge only) |
| TTS | `edge-tts` (free), `elevenlabs` (needs a key), `fish-audio` (cloned voice, needs a key + credit) |

`browser` recognition runs in Chrome/Edge, not in the Electron shell: Chromium there ships without
the speech credentials, so the API exists but every session dies with a `network` error. The
desktop shell therefore starts on `faster-whisper` (and falls forward from a stored `browser`
preference to the best available server engine). The Electron static server's CSP allows
`blob:` for `script-src`/`worker-src`/`media-src`; without that, the microphone capture worklet
cannot load and server STT hears nothing.

### 6.2 How the selection reaches the server

1. The client opens `ws://<host>/voice` and sends one config frame per session:
   `{"type":"config","stt":"faster-whisper","tts":{"engine":"fish-audio","voice":"<voice id>"}}`.
2. The server selects the providers, builds the engines, and answers with a session frame that
   reports the active engine and its fallback.
3. Browser-STT turns additionally carry the TTS choice on the text frame:
   `{"type":"text","text":"...","tts":{"engine":"fish-audio","voice":"<voice id>"}}`.
4. If a selected provider is unavailable, the server degrades to the configured fallback instead
   of leaving the session mute.

### 6.3 Fish Audio (cloned voice)

- The API key must be present in the root `.env` as `FISH_AUDIO_API_KEY`.
- The cloned voice is referenced by `reference_id` (`FISH_AUDIO_VOICE_ID`); in Settings, paste the
  id and use **Insert** to store it.
- **The account needs credit.** A request without credit returns HTTP 402
  (`Insufficient API credit`), which the adapter maps to a clear "insufficient credit" error and
  the session falls back to Edge TTS. Add credit at <https://fish.audio/app/developers>.
- `model_id` is only sent when `FISH_AUDIO_MODEL_ID` is set; leave it empty to use the account default.

### 6.4 faster-whisper (local STT)

The `base` model is downloaded from HuggingFace on first use (needs internet access). If the
download fails, pick `browser` or `groq` in Settings instead.

### 6.5 Quick check of the voice stack

```powershell
# which providers are usable right now
Invoke-RestMethod http://127.0.0.1:8000/voice/options | ConvertTo-Json -Depth 6

# one-shot TTS through the Edge fallback
Invoke-WebRequest -Uri http://127.0.0.1:8000/voice/tts -Method Post `
  -ContentType "application/json" -Body '{"text":"hello","engine":"edge-tts"}' -OutFile tts.mp3
```

---

## 7. Verification performed

| Check | Result |
| --- | --- |
| `ruff check app` + voice test files | clean |
| `pytest tests/unit` | `2629 passed, 3 skipped, 1 failed` (the failure is an unrelated OpenRouter model-prefix assertion in a user-modified test) |
| `npx tsc --noEmit -p tsconfig.json` (UIPROTO) | clean |
| `npm test` (UIPROTO) | `2 files / 6 tests passed` |
| `npm run lint` (UIPROTO) | **fails repo-wide** - see troubleshooting |

---

## 8. Troubleshooting

**`npm run lint` reports thousands of Prettier errors**
The working tree of `UIPROTO` uses CRLF line endings and 4-space indentation while the Prettier
config expects LF and 2 spaces. Untouched files (`src/lib/queries.ts`, `src/lib/events.ts`,
`src/lib/auth.tsx`) fail the same way, so this is pre-existing and not caused by the voice work.
Use `npx tsc --noEmit` and `npm test` as the meaningful client gates.

**Redis: `unknown command 'HELLO'`**
Use `?protocol=2` in `REDIS_URL` (the Windows Redis 3.0 build is RESP2 only).

**`connection refused` on startup / `/health` unhealthy**
PostgreSQL is stopped on this laptop: use the SQLite + Redis process-environment overrides from
section 3.1.

**Fish Audio returns 402**
The key is valid but the account has no credit. Add credit at
<https://fish.audio/app/developers>; until then the session uses the Edge TTS fallback.

**Groq STT reports "not configured"**
`GROQ_API_KEY` is set on this machine (in the Windows user environment), so a fresh process sees
`groq` as available. If a *long-lived* shell or the Electron process was started before the key was
added, it keeps the old (empty) value: the process environment beats every `.env` file. Close and
reopen the shell (or launch with `$env:GROQ_API_KEY=...`), and confirm with
`curl http://127.0.0.1:8000/voice/options`.

**Electron: "published bundle not found"**
`server/app/static/index.html` is missing - run the UIPROTO build, or start the Vite dev server
and use `npm run start:dev`.

**Voice settings look ignored**
Check the env precedence in section 2.1 - the root `.env` overrides `server/.env`, and the process
environment overrides both. A stale `STT_ENGINE` / `TTS_ENGINE` / `VOICE_ENABLED` left over from
`setx` in a long-running shell is the usual cause; start the server from a fresh shell or pass the
values inline for that launch.

A pick the server cannot run is *not* silently substituted any more. The live client checks the
choice against `/voice/options` and refuses to start with the engine's reason, so a picker value
either works or explains why it cannot. Engine selection can be verified without a browser:

```powershell
curl.exe -s -D - -o tts.mp3 -H "Content-Type: application/json" `
  -d '{"text":"hello","engine":"edge-tts"}' http://127.0.0.1:8000/voice/tts
# X-TTS-Engine: edge-tts        (fish-audio reports fish-audio>edge-tts when it 402s)
```

