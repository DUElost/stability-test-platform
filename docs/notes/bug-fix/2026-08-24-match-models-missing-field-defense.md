# 前端 match_models 缺失字段防御（前后端版本不匹配）

Status: implemented
Class: bug-fix

## Decision

`/projects` 列表与详情页渲染 `project.match_models` 时改为 `(project.match_models ?? []).length` /
`.join` 防御形式；`types.ts` 中 `Project.match_models` 标注为可选。

改动：
- `frontend/src/pages/projects/ProjectsPage.tsx`（列表卡片「映射型号」）
- `frontend/src/pages/projects/ProjectDetailPage.tsx`（详情「已映射型号」）
- `frontend/src/utils/api/types.ts`（`match_models?: string[]`）

## 为什么

生产控制面（172.21.x.x）后端**未部署** `0e17f55`（首次加入 `match_models` 字段的
commit）之前，`GET /projects` 响应里项目对象**没有 `match_models` 字段**（实测 8/8 全缺）。
而前端构建产物已是消费该字段的新代码——`ProjectsPage.tsx:294` 原写
`project.match_models.length`，对 undefined 调 `.length` → `Cannot read properties of
undefined (reading 'length')` → React ErrorBoundary 白屏报错。

根因是**前后端版本不匹配**（前端新、后端旧），不是后端 500。同类风险字段以 `?? []`
兜底即可，无需强制后端回滚。

技术细节：修复曾先试 `?.` 写法（`project.match_models?.length`），tsc 仍报 TS18048——
因为三元分支内 `.join` 对可能 undefined 的 `match_models` 访问未受保护。改用
`(project.match_models ?? []).length` 显式兜底后 tsc 通过（`--strict` 下 `?? []`
是唯一干净的窄化方式）。

## Alternatives

- 后端回滚/补部署：不选——治标且动生产。
- 只在 types 标注 optional 不修消费点：不选——会留下运行时崩溃。
- `?.` 链：被 tsc TS18048 否决（分支内后续访问仍报错）。

## Verification

- 生产后端实测：`GET /projects` 8 个项目全无 `match_models`，修复后前端 `/projects`
  与 `/projects/HONOR-MLD` 均正常渲染「尚未映射型号」，console 无
  `Cannot read properties of undefined (reading 'length')`。
- tsc --noEmit 通过；eslint 通过；vitest（ProjectsPage 12 + ProjectDetailPage 5）通过。

## Revisit

生产后端部署含 `match_models` 的新版本后，此防御变成纯冗余保护，但保留无害
（前端对可选字段兜底是常规契约防御）。无需移除。
