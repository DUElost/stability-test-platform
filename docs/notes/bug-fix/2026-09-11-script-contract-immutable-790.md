# 脚本契约字段不可原地修改（#790 / R08-F01）

Status: implemented
Class: bug-fix

## Decision

根因：`PUT /api/v1/scripts/{id}` 的字段回写循环包含 `name` / `version` /
`nfs_path` / `content_sha256`，四者无守卫（`default_params` 有 422、
`is_active=False` 有在途引用检查，唯独契约字段可任意改）：

- 改 `content_sha256` / `nfs_path` 等价于 `force_rebaseline`，却**跳过在途
  PlanRun 409 守卫**（该守卫只在 `POST /scripts/scan?force_rebaseline=true`
  路径），审计也无法区分；
- 改 `name` / `version` 让 PlanStep 的 `(script_name, script_version)` 引用键
  失配 → 所有引用该脚本的 Plan 准入 `script_verify_failed`，self-heal 推送
  修不好（推的是磁盘内容，对不上 DB 期望值）——2026-07-31 事故同型。

修复：PUT 对四个契约字段的**变更**返回 422（同值回传不受影响，兼容客户端
回传全量对象）；内容漂移只走 `POST /scripts/scan[?force_rebaseline=true]`，
标识变更用新版本表达。顺带下架因此不可达的 `name/version` 重复性 409 检查。
`display_name/category/description/script_type/param_schema/capabilities`
保持可改（展示与行为参数走既有 `default_params` 守卫）。

## Alternatives

- **仅对「存在 plan_step 引用」的行禁止**：保留「未被引用的脚本可改名」的
  灵活性，但引用关系可随时间建立，改完仍会踩同一事故；且判定引入歧义（引用
  判定与并发新增之间仍有窗口）——采用**一律禁止**（issue 亦倾向此口径）；
- **只禁 `name`/`version`，放行 `nfs_path`/`content_sha256`**：否决——这两者
  正是绕过 force_rebaseline 守卫的路径；
- **改 schema 删字段（`ScriptUpdate` 不再声明契约字段）**：422 由 pydantic
  校验产生，但丢失「同值回传兼容」与显式错误文案；手工守卫更贴合现状。

## Verification

实际运行：

- `pytest backend/tests/api/test_scripts.py backend/tests/api/test_scripts_default_params.py -q`
  → **36 passed**（新增 2 例：四字段各自变更→422 且消息含字段名、行值不变；
  契约字段同值 + 展示字段→200）；
- `ruff check backend/api/routes/scripts.py backend/tests/api/test_scripts.py`
  → All checks passed；
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。

未完成（pending）：

- 无（如后续 UI 需要「改显示名」入口，直接 PUT 展示字段即可，不受影响）。

## Revisit

- 若未来引入「脚本重命名/迁移」正式流程，应走独立端点 + 引用迁移事务（含
  PlanStep 重写与审计），而不是放开本守卫；
- ADR-0021 D9 的「已知缝隙」已在文中标注收口（本单），后续引用该段时以
  收口注记为准。
