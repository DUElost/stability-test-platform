import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: 'http://172.21.10.21:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://172.21.10.21:8000',
        ws: true,
      },
    },
  },
});
