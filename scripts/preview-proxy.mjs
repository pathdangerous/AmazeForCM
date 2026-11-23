// Development-only preview proxies. The original app ports stay unchanged.
import http from "node:http";
import net from "node:net";

function serve(port, targetPort) {
  const server = http.createServer((req, res) => {
    const upstream = http.request({
      hostname: "127.0.0.1",
      port: targetPort,
      path: req.url,
      method: req.method,
      headers: { ...req.headers, host: `127.0.0.1:${targetPort}` },
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
    const upstream = net.connect(targetPort, "127.0.0.1", () => {
      const headers = { ...req.headers, host: `127.0.0.1:${targetPort}` };
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
  server.listen(port, "0.0.0.0", () => {
    console.log(`Preview proxy ${port} -> 127.0.0.1:${targetPort}`);
  });
}

serve(5000, 5173);
serve(3000, 5174);
