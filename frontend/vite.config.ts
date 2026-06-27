import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

function manualChunks(id: string): string | undefined {
  if (!id.includes('/node_modules/')) return undefined;

  if (
    id.includes('/node_modules/react/') ||
    id.includes('/node_modules/react-dom/') ||
    id.includes('/node_modules/react-router-dom/') ||
    id.includes('/node_modules/react-router/')
  ) {
    return 'vendor-react';
  }
  if (id.includes('/node_modules/@tanstack/react-query/')) {
    return 'vendor-query';
  }
  if (id.includes('/node_modules/lucide-react/')) {
    return 'vendor-ui';
  }
  if (id.includes('/node_modules/@xterm/')) {
    return 'vendor-xterm';
  }
  if (id.includes('/node_modules/recharts/')) {
    return 'vendor-recharts';
  }
  if (id.includes('/node_modules/@radix-ui/')) {
    return 'vendor-radix';
  }
  if (id.includes('/node_modules/socket.io-client/')) {
    return 'vendor-socket';
  }
  if (id.includes('/node_modules/date-fns/')) {
    return 'vendor-date-fns';
  }
  if (id.includes('/node_modules/@dnd-kit/')) {
    return 'vendor-dnd';
  }
  return undefined;
}

export default defineConfig(({ mode }) => {
  // 加载环境变量，mode 会有不同的前缀
  const env = loadEnv(mode, process.cwd(), '');

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks,
        },
      },
    },
    server: {
      port: 5173,
      host: true,
      proxy: {
        '/api': {
          target: env.VITE_API_BASE_URL || 'http://localhost:8000',
          changeOrigin: true,
        },
        '/ws': {
          target: env.VITE_WS_BASE_URL || 'ws://localhost:8000',
          ws: true,
        },
        '/socket.io': {
          target: env.VITE_API_BASE_URL || 'http://localhost:8000',
          ws: true,
          changeOrigin: true,
        },
      },
    },
  };
});
