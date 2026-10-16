// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - TanStack devtools (dev-only, first), tanstackStart, viteReact, tailwindcss, tsConfigPaths,
//     nitro (build-only using cloudflare as a default target), VITE_* env injection, @ path alias,
//     React/TanStack dedupe, error logger plugins, and sandbox detection (port/host/strictPort).
//
// ULTRON overrides (below):
//   - tanstackStart.spa: build a client-only SPA shell so the FastAPI server can serve the
//     static bundle at "/" with no Node runtime.
//   - nitro.preset "static": emit a plain static site (.output/public) to copy into
//     server/app/static.
//   - vite.server.proxy: during `vite dev`, forward the ULTRON API prefixes to the backend on
//     http://127.0.0.1:8000 so relative fetches (/chat, /auth/login, /events, …) work unchanged.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

const BACKEND = "http://127.0.0.1:8000";

// Every root-mounted ULTRON router / probe the UI talks to.
const API_PREFIXES = [
  "/auth",
  "/chat",
  "/events",
  "/health",
  "/ready",
  "/metrics",
  "/agents",
  "/tasks",
  "/tools",
  "/nodes",
];

export default defineConfig({
  tanstackStart: {
    // Redirect TanStack Start's bundled server entry to src/server.ts (our SSR error wrapper).
    // nitro/vite builds from this
    server: { entry: "server" },
    // Client-only SPA: one HTML shell serves every path, no per-route SSR.
    spa: { enabled: true },
  },
  // SPA mode has no server entry, so skip Nitro entirely and keep the static client build.
  nitro: false,
  vite: {
    server: {
      proxy: Object.fromEntries(
        API_PREFIXES.map((prefix) => [
          prefix,
          { target: BACKEND, changeOrigin: true, ws: prefix === "/nodes" },
        ]),
      ),
    },
  },
});
