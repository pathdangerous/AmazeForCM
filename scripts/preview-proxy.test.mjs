import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import net from "node:net";
import { createPreviewServer, targetPort } from "./preview-proxy.mjs";

test("only the market path selects the consumer upstream", () => {
  for (const path of ["/market/", "/market/assets/a.js", "/market/?token=abc"]) {
    assert.equal(targetPort(path), 5174);
  }
  for (const path of ["/", "/api/health", "/marketplace/", "/?next=/market/"]) {
    assert.equal(targetPort(path), 5173);
  }
});

test("HTTP and WebSocket requests keep their full path and reach the selected upstream", async () => {
  const upstreams = [5173, 5174].map((port) => {
    const server = http.createServer((req, res) => res.end(`${port} ${req.url} ${req.headers.host}`));
    server.on("upgrade", (req, socket) => {
      socket.end(`HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nX-Target: ${port}\r\nX-Path: ${req.url}\r\n\r\n`);
    });
    return server;
  });
  const proxy = createPreviewServer();
  try {
    await Promise.all(upstreams.map((s, i) => new Promise(resolve => s.listen(5173 + i, "127.0.0.1", resolve))));
    await new Promise(resolve => proxy.listen(0, "127.0.0.1", resolve));
    const port = proxy.address().port;
    for (const [path, expected] of [["/", 5173], ["/market/?a=1", 5174]]) {
      const response = await fetch(`http://127.0.0.1:${port}${path}`);
      assert.equal(await response.text(), `${expected} ${path} 127.0.0.1:${expected}`);
      const upgraded = await new Promise((resolve, reject) => {
        const socket = net.connect(port, "127.0.0.1", () => {
          socket.write(`GET ${path} HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n`);
        });
        let data = "";
        socket.on("data", chunk => { data += chunk; });
        socket.on("end", () => resolve(data));
        socket.on("error", reject);
        socket.setTimeout(3000, () => socket.destroy(new Error("Upgrade timed out")));
      });
      assert.match(upgraded, new RegExp(`X-Target: ${expected}`));
      assert.ok(upgraded.includes(`X-Path: ${path}`));
    }
  } finally {
    await Promise.all([proxy, ...upstreams].map(s => new Promise(resolve => s.close(resolve))));
  }
});
