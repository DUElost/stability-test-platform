# 设备页未归属筛选 + 归属来源三态标记

Status: implemented
Class: feature

ADR-0029 项目域 P0 第二步（`feat/project-devices-unassigned`）：解决五个域里
最直接的可用性缺口——117 台未归属设备在设备页**根本筛不出来**（筛选下拉只列
USER 项目，`project_key=<未知>` 一律 404，且行内无任何归属信息）。

## Decision

**后端**

1. `GET /api/v1/devices` 新增 `unassigned=true` 参数（`project_id IS NULL`），
   与 `project_key` 互斥（400，互斥校验在 key 存在性检查**之前**——参数组合
   错误优先于资源校验）；未知 key 仍 404（原有语义不变）。
2. `DeviceOut` 新增 `attribution_source` 派生三态：
   - `rule`：型号精确命中该项目 `match_models`（精确匹配，非前缀推断）；
   - `manual`：有归属但型号不在规则（人工/批量归入、SEED 回填如 LEGACY）；
   - `unassigned`：`project_id IS NULL`。
   由 `_fill_project_key` 统一填充（list / get / bulk 三处）；bulk 归入响应用
   局部 project 变量直接算（joinedload 关系在赋值后 stale，不读 device.project）。

**前端**

3. `ProjectFilterSelect` 新增 `showUnassigned` prop（默认 false，仅设备页开），
   追加「未归属」选项，哨兵值 `UNASSIGNED_FILTER_VALUE='__unassigned__'`——
   设备页选中时切 `?unassigned=true`，不发给后端当 project_key。Plan/结果页
   不受影响（Plan 归属 ≠ 设备归属，无「未归属」语义）。
4. 设备行内：未归属 → 黄色醒目「未归属」badge；归属 + 来源 rule → 「规则」
   绿标；manual → 「手动」标；项目 key badge 照旧。P1 规则表落地后
   `manual` 语义升级为「钉住」。
5. `deviceKeys.list` query key 加 unassigned 维度，避免筛选切换共用缓存。

## Alternatives

- **前端本地过滤「未归属」**：设备页只拉全量（1200），可以在内存过滤——
  但拉全量本身就是 1200 上限的脆约定，且后端过滤是后续规则表场景的公共
  能力，成本几乎为零。放弃。
- **`unassigned=true` 与 `project_key` 同时传时 project_key 优先**：静默吞掉
  参数组合错误；400 显式报错更符合「防拼错 key 静默空列表」的既有取向。
- **来源标记直接读 device 表新列**：P0 不改 schema（P1 才加 `project_pinned`），
  从 match_models 派生即可覆盖当前所有语义。

## Verification

- 后端：`test_devices.py::TestProjectAttribution` 3 个新测试（三态断言 /
  unassigned 过滤 / 互斥 400）；全套 20 passed；`test_project_routes.py`
  39 passed（相邻）；ruff 全过。
- 前端：`type-check`（tsc --noEmit）通过；devices 域 vitest 13 passed；
  eslint 改动文件全过。全量 vitest 曾因并行会话重装依赖（vitest 4.1.10→
  4.1.11 且 `@/utils/auth` alias 瞬时失效）出现与本次改动无关的批量失败，
  依赖稳定后复跑确认。

## Revisit

P1 `project_pinned` 列落地后：`manual` 语义细分为「钉住」与「规则外手动」，
badge 文案与后端派生逻辑需同步；若规则表成为唯一写入面，`rule` 的判定
源从 `match_models` 切到规则表。
