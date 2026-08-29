import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
    coverage: {
      // 只出终端表格。默认 reporter 是 ['text','html','clover','json'],
      // 会在 frontend/coverage/ 落地一批产物 —— CI 的日志要的是 text,
      // 其余三种纯属浪费;而且那批产物会被 knip 判成「未被引用的文件」,
      // 让阻塞门禁 lint 变红(碰过一次)。
      // 本地要看 HTML 明细:`npx vitest run --coverage --coverage.reporter=html`
      reporter: ['text'],
    },
  },
});
