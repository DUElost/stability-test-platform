# 归档守卫补齐 + 解档端点 + SEED 退场路径（#644 建议顺序第 3 步）

日期：2026-08-31 · 类型：feature · 关联：#644 复核 · PR：#655

## 决定了什么

1. **归档 = 冻结，四端点补齐**：`rename` / `map preview` / `map apply` /
   `remove-rule` 对 ARCHIVED 项目一律 409（`_require_active_project`，
   「archived project is read-only; unarchive to modify」）。此前只有
   `update_project` 一处检查，归档项目仍可改名、继续映射型号。
2. **解档端点 `POST /{key}/unarchive`**：ARCHIVED → ACTIVE（admin，审计
   `unarchive_project`，emit `unarchived`）。守卫补齐的必要另一半——
   没有解档，「归档 = 冻结」是单行道，误归档无法撤销。
3. **SEED 显式放弃（退场路径）**：archive 对 SEED 放行（去掉
   `_require_user_project`）——v2.5 后 HONOR-MLD / ZTE-Z258 等空标签
   （0 成员行 0 设备）的「有终点的待办队列」终于有终点；promote 对归档
   SEED 的 409 守卫保留（语义自洽）。
4. **待转正队列默认只列 ACTIVE**：`?source=seed` 不加 `status` 时过滤
   ARCHIVED——放弃的标签不再占队列名额；显式 `?status=ARCHIVED` 仍可
   复查（与 `?source=user` 的全量语义有意的差异：队列 = 待办视图）。

## 放弃的备选

- **delete SEED 端点**：归档（软删除）足够，历史审计/追溯保留；「SEED
  行占用 key」无冲突风险（key 全局唯一，未转正行不挡新项目——promote
  文档已注明不能新建同 key USER 行是唯一约束所致）
- **list 默认全过滤 ARCHIVED（user/all 也过滤）**：改动面大（五个页面
  依赖全量），且「归档项目可筛出复查」是既有语义（status_filter 测试）；
  只改待办队列口径

## 如何验证

- 后端：`TestArchiveGuards` 5 例（只读端点 409 / 解档恢复 + 审计 / 未归档
  409 / 非 admin 403 / seed 列表默认 ACTIVE）+ `test_archive_seed_allowed`
  （SEED 归档 200 + LEGACY promote 422 先命中）+ 全文件 61 例
- 前端：DetailPage 归档态恢复按钮 + ProjectsPage seed「放弃」按钮，32 例
  （项目页）+ 全量 644 例；tsc/eslint/ruff 干净
- 生产部署：control-plane 常规（后端 restart + 前端换包），无迁移、无
  Agent 热更新

## 何时重议

- 出现「归档项目恢复后归属被改」的真实事故 → 考虑归档快照冻结（当前
  归档只冻结 UI 操作，DB 行仍可被历史链路触碰）
