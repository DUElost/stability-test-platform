// ESLint flat config（ESLint 10）。
//
// 规则取向：只保留「能指向真实缺陷」的项（未使用变量、hooks 依赖、
// 意外的 any 扩散），纯风格项一律关掉。CI 以 --max-warnings 0 阻塞
// （ci.yml §lint），本地与 CI 结果一致。
import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';

export default tseslint.config(
  {
    ignores: [
      'dist/**',
      'dist-prod/**',
      'node_modules/**',
      'coverage/**',
      '*.config.js',
      '*.config.ts',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.es2021 },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,

      // react-hooks v7 的编译器级规则一次性暴露 64+ 处存量（set-state-in-effect /
      // refs / purity 等），与本 majors 迁移解耦：先显式关闭并另开 issue 分批修复，
      // 避免把依赖升级 PR 膨胀成行为改造。第一批已恢复 refs/purity/immutability/
      // preserve-manual-memoization；本批恢复 set-state-in-effect。
      // incompatible-library：@tanstack/react-virtual 返回不可 memoize 的函数，
      // 编译器无法优化（非代码缺陷），保持 off（#260 记录）。
      'react-hooks/incompatible-library': 'off',

      // 未使用的变量/导入 —— 死代码信号,`_` 前缀表示刻意忽略
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],

      // any 会让 types.ts 那份"前端类型权威源"失去意义,但存量 98 处,
      // 先降为 warn 计数,不硬拦。
      '@typescript-eslint/no-explicit-any': 'warn',

      // Fast-Refresh 边界:组件文件里混导出非组件会静默破坏热更新
      'react-refresh/only-export-components': [
        'warn',
        {
          allowConstantExport: true,
          // 这些 hooks/工具函数与组件同文件是刻意为之（context/hooks/ui 原语、
          // 终端清理、矩阵选择工具等），显式允许以保留 fast refresh 对组件本身
          // 的保护，而不是整个关闭规则。
          allowExportNames: [
            'createQueryClient',
            'clearAppQueryCache',
            'WIZARD_STEPS',
            'buildMatrixVirtualRows',
            'applyMatrixSelection',
            'disposeAllTerminals',
            'releaseTerminal',
            'gateElapsedSeconds',
            'isGateStale',
            'badgeVariants',
            'buttonVariants',
            'resolveStatusEntry',
            'useHeaderSlot',
            'resolveTheme',
            'applyResolvedTheme',
            'useTheme',
            'useConfirm',
          ],
        },
      ],

      // 纯风格项:交给人和 review,不由 linter 制造噪音
      'no-undef': 'off',            // TS 自身已覆盖,且对 tsx 有误报
    },
  },
  {
    // XTerminal 要剥离 ANSI 转义序列,正则里出现控制字符是本职工作,
    // 不是笔误 —— no-control-regex 在这里全是误报。
    files: ['**/XTerminal.tsx'],
    rules: { 'no-control-regex': 'off' },
  },
  {
    // shadcn/ui 原语按上游模板生成(空 interface 继承是其惯用写法),
    // 属于 vendored 代码,不按本仓规则改写。
    files: ['src/components/ui/**'],
    rules: { '@typescript-eslint/no-empty-object-type': 'off' },
  },
  {
    // 测试文件放宽:mock 与断言里 any / 非空断言是常态
    files: ['**/*.test.{ts,tsx}', '**/test/**', '**/__tests__/**'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-non-null-assertion': 'off',
    },
  },
);
