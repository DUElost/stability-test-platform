# 前端死代码门禁 knip（files + dependencies 粒度）

Status: implemented
Class: process

## Decision

前端接入 knip，进 `run_gates check:quick`。门禁粒度收敛为
`--include files,dependencies`：只报**未用文件、未用/未声明依赖**。

- tsc 的 `noUnusedLocals`/`noUnusedParameters` 已覆盖局部未用变量，knip
  补跨文件盲区（整文件没人 import、依赖没人引）。
- 逐符号 export 检查（exports/types/duplicates）**排除在门禁外**：
  `src/utils/api/types.ts` 是后端 Pydantic schema 的权威镜像（CLAUDE.md
  约定），大量类型为契约完整性存在；shadcn 组件再导出同理。首轮报告
  234 条逐符号告警，绝大部分属此类设计噪声。

首轮清理（同 PR）：删除 7 个死组件文件（DeviceGrid / DeviceMonitorPanel /
DeviceSelector / PlanExecuteWizardNav / LogViewer / HostCard /
DeviceToolbar）、移除 6 个未用依赖（@dnd-kit/* ×3、react-checkbox、
react-table、react-resizable-panels）及 vite.config.ts 中对应
manualChunks 分支；补上遗漏未声明的 @radix-ui/react-dialog。

## Alternatives

- 全量严格模式（含逐符号 export）：拒绝。triage 成本与契约镜像约定冲突，
  收益边际。
- jscpd（跨文件重复检测）：拒绝。38 个脚本版本目录（ADR-0020 不可变）
  与测试是契约性重复，排除后信号薄。
- 后端 vulture：暂不常驻，先跑一次性只读审计（见 vulture 审计结果）再定。

## Verification

- 本地：`npm run knip`（frontend）0 finding，exit 0；
- tsc / eslint 在删除后仍全绿。

## Revisit

vulture 只读审计（2026-08-14，min-confidence 90，排除 scripts/tests）共
70 条，全部为 unused variable：约 64 条是测试文件的 pytest
context-manager 惯用法（mock_sleep/td/test_user 等），6 条是生产代码的
未用异常变量；**0 个未用函数/类**。结论：后端不引入 vulture 常驻门禁
（信号薄，triage 不值）；未用异常变量属顺手清理项，不单独立项。
