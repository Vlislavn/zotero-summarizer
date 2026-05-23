import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite config for the zotero-summarizer React frontend.
// - In dev, Vite proxies /api/* to the FastAPI backend on :8000.
// - In prod, FastAPI mounts the build at the root so the SPA owns the
//   whole tab list (Today / Annotate / Settings + Power tools).
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
      },
    },
  },
});
