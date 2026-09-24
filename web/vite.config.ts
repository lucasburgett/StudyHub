import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend (FastAPI) listens on 8000. In development Vite proxies /api to it;
// `vite preview` reuses the same proxy. In production the backend serves web/dist.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000' },
    },
  },
  build: {
    outDir: 'dist',
    // One bundle is fine for a local app; KaTeX and the markdown pipeline make up most of it.
    chunkSizeWarningLimit: 1000,
  },
})
