# minimatch globparts 放大修复（Dependabot #26 / GHSA-g3hv-75wg-m249）

Status: implemented
Class: bug-fix

## Decision

在 `frontend/package.json` 为三条已确认的开发依赖链增加 scoped overrides，把
minimatch 固定到 `11.0.3`：

- `eslint` → `minimatch`
- `@eslint/config-array` → `minimatch`
- `@typescript-eslint/typescript-estree` → `minimatch`

选择 scoped overrides 而不是全局 override：覆盖只作用于上述三条开发依赖链，不扩大到
其他消费者或未来的业务依赖，也不以整批依赖升级掩盖安全修复的兼容性风险。minimatch
不进入业务 `dependencies`；三个消费者保持原版本；lock 由 npm 正常重生成，未手工修改
版本、下载地址或 integrity。

修复层级是 `frontend` 开发依赖的传递依赖，不涉及生产运行时与业务代码。

**过渡方案与退出条件**：当某个消费者原生声明 `minimatch >= 11.0.1` 时，删除该消费者
对应的 override 并重跑本 note 的 A–C 验收；三条链均满足后删除整个 `overrides` 块。
在此之前，override 是安全修复的过渡载体，不视为长期依赖形态。

涉及文件：

- `frontend/package.json`
- `frontend/package-lock.json`
- 本 note

## Alternatives

- **升级三个消费者**：eslint、@eslint/config-array、@typescript-eslint/typescript-estree
  当前仍声明 `^10.x` minimatch 依赖；升级会引入更大的规则/API 兼容面，且不是修复本
  告警的必要条件。拒绝。
- **全局 `minimatch` override**：会覆盖所有消费者（包括未来新增的业务依赖），超出已
  确认的三条开发链，也失去“只修已确认面”的边界。拒绝。
- **`npm audit fix --force` 或整体刷新依赖**：会把安全修复包装成不可审阅的依赖升级，
  无法保证 lock 差异只含必要闭包。拒绝。
- **接受告警**：中危 DoS，且三条链均可通过 scoped override 安全修复。拒绝。

## Verification

环境：Node `v22.23.4`（CI 同主版本 22），npm `10.9.8`；lock 由 npm `11.21.1`
在 Node `v22.23.4` 下生成（原因见“偏离说明与观察”）。所有安装与检查均在独立 worktree
`.wt/minimatch-26` 中完成，不复用共享工作树的 `node_modules`。

### A. 依赖解析

- 干净 `npm ci`：`added 516 packages, and audited 517 packages in 42s`。
- `npm ls minimatch --all` 退出码 0，三条链均为 `11.0.3`：

  ```text
  eslint@10.12.0 overridden
  ├─ @eslint/config-array@0.23.5 overridden
  │  └── minimatch@11.0.3 deduped
  └── minimatch@11.0.3 overridden
  typescript-eslint@8.71.0
  └─ @typescript-eslint/typescript-estree@8.71.0 overridden
     └── minimatch@11.0.3 deduped
  ```

- `npm ls --all` 退出码 0，无 invalid 依赖。
- lock 对比（`HEAD` vs 工作区）：新增 `node_modules/@isaacs/nonrecursive-glob-match@1.0.3`；
  `node_modules/minimatch` 由 `10.2.6` 变为 `11.0.3`。逐包比较后，**没有其他包**
  的 `version` 或 `integrity` 发生变化；`brace-expansion` 仍为 `5.0.12`，无需变动。
- `npm audit`：`GHSA-g3hv-75wg-m249` 已消失。剩余告警为无关的 `zod`
  （`GHSA-mrhx-c7xc-m3w5`，high），按方案单独报告，不在本单元处理。

### B. 有界安全反例

以隔离子进程、15s 超时和 128 MiB V8 堆上限执行：

```bash
timeout 15s node --max-old-space-size=128 --input-type=module -e '
import { Minimatch } from "minimatch";
const pattern = Array(8).fill("**/../a/b").join("/");
const m = new Minimatch(pattern, { optimizationLevel: 2, braceExpandMax: 64 });
console.log(JSON.stringify({ setLength: m.set.length }));
'
```

- 旧版 `minimatch@10.2.6`（临时目录隔离安装）：`setLength = 256`，与规划阶段实测一致。
- 修复后 `minimatch@11.0.3`：编译完成，`setLength = 64`（≤ 64）。
- 正常模式匹配（同一 11.0.3 安装）全部符合预期：
  `src/**/*.ts` 匹配 `src/a/b.ts`、不匹配 `src/a/b.js`；
  `**/*.{ts,tsx}` 匹配 `src/x/y.tsx`；
  `a/**/b` 匹配 `a/x/y/b`；
  `*.js` 不匹配 `src/a.js`。
- 额外兼容矩阵（11 个用例：partial、dot、braceExpandMax、flipNegate、nocomment、
  nonegate、函数式 `minimatch()`、`set.length`）在 10.2.6 与 11.0.3 上输出完全一致。

### C. 消费者兼容性

实际调用点（安装后的包）：

- `eslint/lib/eslint/eslint-helpers.js`：`new Minimatch(pattern, { dot: true })`，
  并使用 `matcher.match(entry.path, true)` 的 partial 形式；11.0.3 的 partial 行为与
  10.2.6 一致。
- `@eslint/config-array/dist/cjs/index.cjs`：`new minimatch.Minimatch(pattern, { dot: true })`
  后调用 `matcher.match(filepath)`。
- `@typescript-eslint/typescript-estree/dist/useProgramFromProjectService.js`：
  函数式 `minimatch(filePath, pattern, { dot: true })`。

未发现对 11.x 已删除 API（如 `matchOne`）的调用；`partial` 仍受 11.0.3 支持并已对照。

前端五项（Node 22）：

- `npm run lint -- --max-warnings 0`：退出码 0；ESLint 实际检查 `404` 个文件、
  `0` error / `0` warning。`isPathIgnored` 对 `dist/`、`dist-prod/`、
  `node_modules/`、`coverage/` 及 `*.config.ts` 均返回 true。
- `npm run --silent knip`：退出码 0。
- `npm run type-check`：退出码 0。
- `npm run build`：退出码 0（vite build 完成）。
- `npx vitest run`：`152` 个测试文件、`1333` 个测试全部通过。

### D. 仓库门禁

- `./scripts/project_python.sh scripts/run_pytest.py tests/ -q`：
  `2429 passed, 18 skipped, 18 warnings in 636.23s`，退出码 0。18 个跳过全部来自
  `tests/test_seed_revision_version_guard.py` 的历史版本分支（ADR-0051 Phase 3 后
  不在族树中），非环境或生产库跳过。
- `./scripts/project_python.sh scripts/run_gates.py check:quick`：16 个 gate 全部通过，
  退出码 0。

### required CI

按 PR 实际 HEAD 另行核验：`lint`、`CodeQL`、`pr-typecheck`、`pr-compileall`、
`pr-agent-tests`、`pr-migrate-empty-db`。本 note 不把本地成功当作 CI 通过。

## 偏离说明与观察

- **基线移动**：开工时 `origin/main` 已从规划基线 `a9cac01b` 前进到 `e9ed7aa6`。
  重新核对后三个消费者与 minimatch 版本均未变化（仍为 eslint 10.12.0、
  @eslint/config-array 0.23.5、@typescript-eslint/typescript-estree 8.71.0、
  minimatch 10.2.6），不属于退回条件中的“新增消费者”。期间落地的其他前端依赖版本
  变化与 minimatch 无关。
- **lock 生成器选择**：npm 10.9.8 重写 lock 时会删除若干可选平台包的 `libc` 元数据，
  产生与安全修复无关的 diff；改用 npm 11.21.1（同一 Node 22.23.4）生成 lock 后，
  diff 仅含 minimatch 及其必要新增传递依赖。`npm ci` 与全部检查仍使用 npm 10.9.8，
  其他包的 version/integrity 均未变化。
- `npm ls` 将三个消费者标记为 `overridden`，这是 scoped override 的预期显示。
- 本单元不部署、不重启服务、不操作 auto-merge、不合并、不转 ready、不 dismiss 告警。

## Revisit

- 任一消费者原生支持 `minimatch >= 11.0.1`：删除该消费者的 override，重跑 A–C。
- 三条链均原生支持后：删除整个 `overrides` 块。
- minimatch 12、eslint 11 或 typescript-eslint 9 变更 minimatch API / glob 语义时：
  重新执行消费者调用点扫描与本 note 的 B/C 验收。
