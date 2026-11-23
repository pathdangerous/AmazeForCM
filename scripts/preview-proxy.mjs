// Development-only single entrance. Keep /market/ in upstream paths for Vite's base.
import http from "node:http";
import net from "node:net";
import { pathToFileURL } from "node:url";

export function targetPort(path) {
  return new URL(path, "http://localhost").pathname.startsWith("/market/") ? 5174 : 5173;
}

export function createPreviewServer() {
  const server = http.createServer((req, res) => {
    if (new URL(req.url, "http://localhost").pathname === "/market") {
      res.writeHead(308, { Location: req.url.replace("/market", "/market/") });
      res.end();
      return;
    }
    const port = targetPort(req.url);
    const upstream = http.request({
      hostname: "127.0.0.1",
      port,
      path: req.url,
      method: req.method,
      headers: { ...req.headers, host: `127.0.0.1:${port}` },
    }, (reply) => {
      res.writeHead(reply.statusCode, reply.headers);
      reply.pipe(res);
    });
    upstream.on("error", () => {
      if (!res.headersSent) res.writeHead(502, { "Content-Type": "text/plain" });
      res.end("The development UI is not available.\n");
    });
    req.on("aborted", () => upstream.destroy());
    req.pipe(upstream);
  });
  server.on("upgrade", (req, socket, head) => {
    const port = targetPort(req.url);
    const upstream = net.connect(port, "127.0.0.1", () => {
      const headers = { ...req.headers, host: `127.0.0.1:${port}` };
      upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`
        + Object.entries(headers).map(([key, value]) => `${key}: ${value}\r\n`).join("")
        + "\r\n");
      if (head.length) upstream.write(head);
      socket.pipe(upstream).pipe(socket);
    });
    upstream.on("error", () => socket.destroy());
    socket.on("error", () => upstream.destroy());
    socket.on("close", () => upstream.destroy());
  });
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  createPreviewServer().listen(5000, "0.0.0.0", () => {
    console.log("Preview proxy 5000: /market/ -> 5174; all else -> 5173");
  });
}
