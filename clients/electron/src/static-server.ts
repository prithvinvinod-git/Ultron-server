/**
 * Loopback static file server for the published UIPROTO bundle (T516).
 *
 * The built SPA references its assets with absolute root paths (`/assets/...`),
 * so Electron cannot load it from `file://`. Serving it from `127.0.0.1` keeps
 * every URL identical to the FastAPI deployment: same-origin relative fetches,
 * the SSE client, and the asset graph all work unchanged (§69.3/§69.4).
 *
 * Binds loopback only — never a LAN interface. API proxying (§69.7 reuse of
 * the server contracts) is layered on in T519; this server is static files.
 */
import { createReadStream, statSync } from "node:fs";
import type { Server } from "node:http";
import { createServer } from "node:http";
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

export interface StaticServerHandle {
  server: Server;
  origin: string;
  close: () => Promise<void>;
}

export function startStaticServer(root: string): Promise<StaticServerHandle> {
  const rootAbs = resolve(root);
  const indexFile = resolve(rootAbs, "index.html");

  const server = createServer((req, res) => {
    if (req.method !== "GET" && req.method !== "HEAD") {
      res.writeHead(405, { Allow: "GET, HEAD" }).end();
      return;
    }
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    let pathname: string;
    try {
      pathname = decodeURIComponent(url.pathname);
    } catch {
      res.writeHead(400).end();
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
