import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// `npm run dev` proxies API calls to a locally running backend,
// so the app always talks to its own origin (same as in Docker/nginx).
const apiTarget = process.env.VITE_DEV_API_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 3000,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
});
