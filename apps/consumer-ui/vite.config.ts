import { Agent } from "node:http";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// consumer-ui: fixed port 5174; a taken port is an error. /api goes to the API on port 8000.
// 127.0.0.1 rather than localhost, so Node does not pick IPv6 (::1) while uvicorn listens on IPv4.
// Phase 7.5 (checkpoint 2 follow-up): the proxy keeps its connections to the API alive instead of opening one per
// request, after a screens run followed by story runs failed in the browser with net::ERR_NO_BUFFER_SPACE.
// Dev server only: the built UI has no proxy.
const server = {
  host: "127.0.0.1",
  port: 5174,
  strictPort: true,
  proxy: { "/api": { target: "http://127.0.0.1:8000", agent: new Agent({ keepAlive: true }) } },
};

export default defineConfig({
  base: "/market/",
  plugins: [react()],
  server,
  preview: server,
});
