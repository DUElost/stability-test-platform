import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

function manualChunks(id: string): string | undefined {
  if (!id.includes('/node_modules/')) return undefined;

  // cn() 的三个依赖必须自成一包，且这条规则要排在所有重量级 vendor 之前。
  //
  // 它们没有独立分包时，打包器会把 clsx 并进恰好也依赖它的 vendor-recharts。
  // 而 cn() 被 80+ 个文件引用，design-system/tokens 与 button 又在首屏必经图里，
  // 于是首屏 chunk 会 `import { clsx } from './vendor-recharts'` ——
  // 为一个几百字节的工具函数把 410KB 的图表库拽进冷启动路径（+114KB gzip，
  // 占首屏 31%），连只打开 /login 的用户都要付这笔钱。
  //
  // 回归检测：`npm run build` 后 dist/index.html 的 modulepreload 列表里
  // 不得出现 vendor-recharts / vendor-xterm。
  if (
    id.includes('/node_modules/clsx/') ||
    id.includes('/node_modules/tailwind-merge/') ||
    id.includes('/node_modules/class-variance-authority/')
  ) {
    return 'vendor-cn';
  }
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
        '/socket.io': {
          target: env.VITE_API_BASE_URL || 'http://localhost:8000',
          ws: true,
          changeOrigin: true,
        },
      },
    },
  };
});
