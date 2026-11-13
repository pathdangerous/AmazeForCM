import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// consumer-ui: fixed port 5174; a taken port is an error. /api goes to the API on port 8000.
// 127.0.0.1 rather than localhost, so Node does not pick IPv6 (::1) while uvicorn listens on IPv4.
const server = {
  host: "127.0.0.1",
  port: 5174,
  strictPort: true,
  proxy: { "/api": "http://127.0.0.1:8000" },
};

export default defineConfig({
  plugins: [react()],
  server,
  preview: server,
});
