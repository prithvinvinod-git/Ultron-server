/**
 * Loopback static file server for the published UIPROTO bundle (T516),
 * with the desktop API proxy layered on (T519).
 *
 * The built SPA references its assets with absolute root paths (`/assets/...`),
 * so Electron cannot load it from `file://`. Serving it from `127.0.0.1` keeps
 * every URL identical to the FastAPI deployment: same-origin relative fetches,
 * the SSE client, and the asset graph all work unchanged (§69.3/§69.4).
 *
 * T519: the ULTRON API prefixes (`/auth`, `/chat`, `/events`, ...) are proxied
 * to the configured backend (ULTRON_BACKEND_URL) — REST and SSE as streamed
 * HTTP, `/nodes` and `/voice` WebSocket upgrades forwarded over raw TCP. This
 * is the same reuse the vite dev proxy performs; no new protocol and no
 * duplicated client (§69.7/D042).
 *
 * Binds loopback only — never a LAN interface.
 */
import { createReadStream, statSync } from "node:fs";
import type { Server } from "node:http";
import { createServer, request as httpRequest } from "node:http";
import type { Socket } from "node:net";
import { connect as netConnect } from "node:net";
import { extname, resolve, sep } from "node:path";

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
};

/** Same prefix list as the UIPROTO vite dev proxy (§69.7 reuse). */
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
  "/devices",
  "/voice",
  "/schedules",
  "/notifications",
];

/** Prefixes that carry WebSocket upgrades (node protocol T162, voice WS). */
const WS_PREFIXES = ["/nodes", "/voice"];

export interface StaticServerOptions {
  backendUrl?: string;
}

export interface StaticServerHandle {
  server: Server;
  origin: string;
  close: () => Promise<void>;
}

type Logger = (level: "info" | "error", message: string, extra?: Record<string, unknown>) => void;

function proxyRequest(
  req: import("node:http").IncomingMessage,
  res: import("node:http").ServerResponse,
  backendUrl: string,
  log: Logger,
): void {
  const target = new URL(req.url ?? "/", backendUrl);
  const headers: Record<string, string | string[]> = { ...req.headers, host: target.host };

  const proxyReq = httpRequest(
    {
      hostname: target.hostname,
      port: target.port,
      path: `${target.pathname}${target.search}`,
      method: req.method,
      headers,
    },
    (proxyRes) => {
      log("info", "proxied API response", {
        path: target.pathname,
        status: proxyRes.statusCode ?? 0,
      });
      res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
      proxyRes.pipe(res);
    },
  );

  proxyReq.on("error", (error: Error) => {
    // Honest failure: never fake success (§69.5).
    if (!res.headersSent) {
      res.writeHead(502, { "Content-Type": "application/json; charset=utf-8" });
    }
    res.end(
      JSON.stringify({
        error: {
          code: "BACKEND_UNREACHABLE",
          message: `backend unreachable: ${error.message}`,
        },
      }),
    );
  });

  req.pipe(proxyReq);
}

function proxyUpgrade(req: import("node:http").IncomingMessage, socket: import("node:stream").Duplex, head: Buffer, backendUrl: string, log: Logger): void {
  const target = new URL(req.url ?? "/", backendUrl);
  const port = Number(target.port) || 80;

  const upstream = netConnect({ host: target.hostname, port }, () => {
    const headerLines = Object.entries({ ...req.headers, host: target.host })
      .map(
        ([key, value]) =>
          `${key}: ${Array.isArray(value) ? value.join(", ") : value ?? ""}`,
      )
      .join("\r\n");
    upstream.write(`GET ${target.pathname} HTTP/1.1\r\n${headerLines}\r\n\r\n`);
    if (head.length > 0) upstream.write(head);
  });

  socket.pipe(upstream);
  upstream.pipe(socket);

  upstream.on("error", (error: Error) => {
    log("error", "ws proxy upstream error", { path: target.pathname, error: error.message });
    socket.destroy();
  });
  socket.on("error", () => upstream.destroy());
}

export function startStaticServer(
  root: string,
  options: StaticServerOptions = {},
  log: Logger = () => {},
): Promise<StaticServerHandle> {
  const rootAbs = resolve(root);
  const indexFile = resolve(rootAbs, "index.html");
  const backendUrl = options.backendUrl;

  function isApiPrefix(pathname: string): boolean {
    return API_PREFIXES.some(
      (prefix) => pathname === prefix || pathname.startsWith(prefix + "/"),
    );
  }

  function isWsPrefix(pathname: string): boolean {
    return WS_PREFIXES.some(
      (prefix) => pathname === prefix || pathname.startsWith(prefix + "/"),
    );
  }

  const server = createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    let pathname: string;
    try {
      pathname = decodeURIComponent(url.pathname);
    } catch {
      res.writeHead(400).end();
      return;
    }

    // T519: API prefixes proxy to the backend (REST + SSE stream). This must be
    // checked *before* the GET/HEAD guard: proxied API requests legitimately use
    // POST/PUT/PATCH/DELETE (e.g. `POST /chat/stream`), so guarding methods here
    // would answer every API write with 405 and the app could never chat.
    if (backendUrl && isApiPrefix(pathname)) {
      log("info", "proxying API request", { path: pathname, backendUrl });
      proxyRequest(req, res, backendUrl, log);
      return;
    }

    // Static assets are read-only: anything but GET/HEAD is a client error.
    if (req.method !== "GET" && req.method !== "HEAD") {
      res.writeHead(405, { Allow: "GET, HEAD" }).end();
      return;
    }

    // Traversal guard: resolve against root and require the result inside it.
    const candidate = resolve(rootAbs, `.${pathname}`);
    if (candidate !== rootAbs && !candidate.startsWith(rootAbs + sep)) {
      res.writeHead(403).end();
      return;
    }

    let target = candidate;
    try {
      const st = statSync(target);
      if (st.isDirectory()) target = resolve(target, "index.html");
    } catch {
      // SPA fallback: unknown extension-less paths serve the shell.
      target = extname(pathname) === "" ? indexFile : resolve(rootAbs, pathname);
      try {
        statSync(target);
      } catch {
        res.writeHead(404, { "Content-Type": "text/plain" }).end("not found");
        return;
      }
    }

    const type = MIME[extname(target).toLowerCase()] ?? "application/octet-stream";
    try {
      const st = statSync(target);
      res.writeHead(200, {
        "Content-Type": type,
        "Content-Length": st.size,
        "Content-Security-Policy": [
          "default-src 'self'",
          "script-src 'self' 'unsafe-inline'",
          "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
          "font-src 'self' https://fonts.gstatic.com",
          "img-src 'self' data:",
          "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com",
        ].join("; "),
      });
      if (req.method === "HEAD") {
        res.end();
        return;
      }
      createReadStream(target).pipe(res);
    } catch {
      res.writeHead(404, { "Content-Type": "text/plain" }).end("not found");
    }
  });

  // T519: WebSocket upgrade forwarding (node protocol T162, voice WS).
  server.on("upgrade", (req, socket, head) => {
    const pathname = new URL(req.url ?? "/", "http://127.0.0.1").pathname;
    if (!backendUrl || !isWsPrefix(pathname)) {
      socket.destroy();
      return;
    }
    log("info", "proxying WS upgrade", { path: pathname, backendUrl });
    proxyUpgrade(req, socket, head, backendUrl, log);
  });

  return new Promise((resolvePromise, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address === null || typeof address === "string") {
        reject(new Error("static server bound to an unexpected address"));
        return;
      }
      resolvePromise({
        server,
        origin: `http://127.0.0.1:${address.port}`,
        close: () =>
          new Promise<void>((done) => {
            server.close(() => done());
          }),
      });
    });
  });
}
