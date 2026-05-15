import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite config for the annotation-verdict tool.
// - In dev, Vite proxies /api/* to the FastAPI backend on :8000.
// - In prod, FastAPI mounts the build at URL /annotate, so we set base accordingly.
export default defineConfig({
  plugins: [react()],
  base: '/annotate/',
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
