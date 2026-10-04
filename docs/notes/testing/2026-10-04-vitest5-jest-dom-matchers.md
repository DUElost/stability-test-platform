# Vitest 5 下 jest-dom matcher 走 Matchers<R, T>（#3608）

Status: implemented
Class: testing

关联：[PR #3608](https://github.com/DUElost/stability-test-platform/pull/3608)（Dependabot
frontend-major：Vitest 4.1 → 5.0）。

## Decision

Vitest 5 的 `expect()` 返回 `Assertion<R, T>`（`R` 为 matcher 返回类型，`T` 为
received），自定义 matcher 必须增强 `declare module 'vitest' { interface Matchers<R, T> }`。
它不再读取全局 `jest.Matchers`。

因此：

- 运行时：`frontend/src/test/setup.ts` 改为
  `import '@testing-library/jest-dom/vitest'`（官方 Vitest 入口，内部
  `expect.extend`）。
- 类型：新增 `frontend/src/test/vitest-jest-dom.d.ts`，把
  `TestingLibraryMatchers` 合并进 Vitest 5 的 `Matchers<R, T>`。
  `@testing-library/jest-dom@7.0.1` 自带的 `types/vitest.d.ts` 仍写
  `interface Assertion<T = any>`（单参数、Vitest 4 形状），与
  `Assertion<R, T>` 不能合并，不能单独依赖该文件过 `tsc`。

影响面仅测试类型与 setup 入口；生产代码与 CI 门禁名单不变。

## Alternatives

- **只改 import、不写本地 shim**：否决。本地复现
  `npm run type-check` 仍是 ~1285 条 TS2339（`toBeInTheDocument` 等不在
  `Assertion<void, HTMLElement>` 上），因为 jest-dom 的 Assertion 增强对不上
  Vitest 5 的双参数接口。
- **给每个 `expect(...)` 做 as-cast / 自定义 matcher 包装**：否决。覆盖面是整份
  前端测试，属于类型黑客，且与 Vitest 5 文档的 Matchers 扩展路径相反。
- **钉死 Vitest 4.1.x 等 jest-dom 改类型**：否决。本 PR 的目的就是升 5；shim
  是过渡，jest-dom 一旦按 `Matchers<R, T>` 发布即可删本地 d.ts。
- **在 `tsconfig.json` 设 `"types": ["vitest/globals", "@testing-library/jest-dom"]`**：
  否决。`@testing-library/jest-dom` 主入口类型是 Jest，不是 Vitest；收窄 `types`
  还会挤掉现有的 `vite/client` 自动纳入。

## Verification

- 修复前：`cd frontend && npm run type-check` → 失败，错误均为
  `Property '<jest-dom matcher>' does not exist on type 'Assertion<void, …>'`
  （与 CI `pr-typecheck` run 37151295485 同源）。
- 修复后：`cd frontend && npm run type-check` → 退出码 0
  （`tsc --noEmit` + `tsc --noEmit -p tsconfig.node.json`）。
- 空 `interface Matchers` 合并会触发 `@typescript-eslint/no-empty-object-type`，
  未使用的第二型参会触发 `no-unused-vars`。保留 merge 形状，把型参改成 `_T`，
  并在该 interface 上加 scoped `eslint-disable-next-line`（不关仓库规则）。
- `cd frontend && npm run lint -- --max-warnings 0` → 退出码 0。
- `cd frontend && npm run knip` → 通过。
- `cd frontend && npx vitest run src/components/ErrorBoundary.test.tsx src/components/device/DeviceBulkActionBar.test.tsx`
  → 2 files / 11 tests passed（确认 `/vitest` 运行时入口仍挂 matcher）。

## Revisit

当 `@testing-library/jest-dom` 的 `types/vitest.d.ts` 改为增强
`Matchers<R, T>`（或 Vitest 再次把 `Assertion` 变回可被单参数接口合并的形状）时，
删除 `frontend/src/test/vitest-jest-dom.d.ts`，只保留
`import '@testing-library/jest-dom/vitest'`。
