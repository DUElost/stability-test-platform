# 前端 UI 治理前置契约入库（#4117 U0-1）

Status: implemented
Class: architecture

## Decision

落地 `docs/design/2026-11-23-frontend-ui-governance-contract.md`（v1.0 Accepted），在 `docs/DOC-MAP.md` 登记入口指针，并废止 `frontend/src/design-system/tokens.ts:298-300` 已与现行 `SearchableSelect` 实践冲突的旧 B4 注释。

本次契约固化 Owner 在前端 Phase 0 全量摸底（30 业务路由页 + 262 Open Issues + `stp-ui-review` 8 核心页 32 张 Track B 基线截图）后拍板的 D1–D6 全部方向裁决、视觉密度与状态层级标准、9 页防返工冻结清单，以及后续 Wave 1 静态守卫（只减不增 allowlist）与 B 轨实机验收门槛。

涉及文件：
- `docs/design/2026-11-23-frontend-ui-governance-contract.md`（新增）
- `docs/DOC-MAP.md`（新增前端契约入口登记）
- `frontend/src/design-system/tokens.ts`（更新表单控件注释指向 D1 两档分层）

## Alternatives

1. **直接按口头约定改页面、不先落盘契约文档**：在多 AI 会话并行迭代下，无书面权威源与机械守卫会导致各页再次向不同代际的组件写法漂移（已在 Phase 0 实证为「半迁移断层」）。
2. **全站一刀切消灭原生 `<select>`**：表单短枚举（≤8 项）全面改自定义下拉成本高且破坏表单原生无障碍行为，因此采纳 D1 两档分层。

## Verification

- 按 ADR-0059（`docs/development/writing-conventions.md` §5）完成逐句强度词与术语自查；
- 运行 `.venv/bin/python scripts/run_gates.py check:quick` 与前端 TypeScript 类型检查（`npm run typecheck`）。

## Revisit

- 当 Phase 1 Wave 2（`DevicesPage` 标杆页）在 `stp-ui-review` 完成实机验收定调，或 `[L3]` 议题（如 D3 `ResultsPage` 定位、D5 Phase 2 服务端分页）解锁 9 页冻结清单中的任一页面时，同步修订本契约对应节与版本号。
