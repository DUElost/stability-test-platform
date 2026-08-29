# 归属规则表 + 心跳解析 + 钉住语义（ADR-0029 P1-A）

Status: implemented
Class: feature

P1 第一段（additive migration + 一个 PR）：规则层从 `test_project.match_models`
（JSONB 数组，可重叠、无告警）升格为真表 `project_device_rule`，心跳热路径
开始应用归属规则；批量归入升级为「钉住」语义。归属「靠平台维护、手动核对」
的决策正式落到代码。

## Decision

**1. Migration `c4d5e6f7g8h9`**（多父 down_revision，repo 有三个 alembic head）

- `project_device_rule` 表：project_id / match_type（CHECK MODEL|SERIAL）/
  match_value / is_active / created_at / created_by
- **部分唯一索引 `uq_rule_active`**（`(match_type, lower(match_value)) WHERE
  is_active`）：model → project 在活跃规则内是函数——「MLD_LX2 同时属于
  两个项目」在 DB 层建不出来（此前靠 apply 的应用层 conflicts 409 拦截）
- `device.project_pinned` 列（default false）
- **存量 match_models 灌入规则表**（LATERAL unnest + ON CONFLICT DO NOTHING，
  跨项目重叠的旧数据跳过——由唯一索引兜底）

**2. 解析层 `backend/services/project_attribution.py`**

- `resolve_project_id(db, model)`：活跃规则精确匹配，无命中 → None（显式
  「待归属」，不猜）
- `apply_attribution(db, device)`：pinned 守卫（人工钉住永不被规则覆盖）+
  未命中保持现状（不抹除已有归属——归属错了改规则/改钉住，不是心跳清空）
- `resolve_rules_for_model`：规则变更重算用

**3. 心跳调用点（`heartbeat.py:365` 附近）**

触发条件收窄为「新建 Device / model 变更 / project_id IS NULL」——稳态
三个条件全不满足，零额外查询（与 #73 platform 守卫同一思路）。pinned
守卫在 apply_attribution 内部。

**4. 规则读写切表（`projects.py`）**

- map/apply：写 `project_device_rule` 行（冲突 409 双保险：应用层查 +
  DB 唯一索引）；受影响设备重算归属、**跳过 pinned**
- promote：match_models 预填改规则表写入
- 读侧 `match_models` 从规则表派生（`_rule_values_for_project`，P1-B drop 列
  前的过渡兼容）
- **批量归入升级为钉住**（devices.py bulk）：`project_id + project_pinned =
  true`；幂等 skip 条件改为「已归入且已钉住」

**5. 归属来源四态**：`attribution_source` 增加 `pinned`（优先级：pinned >
rule > manual > unassigned），前端 badge「钉住」（蓝标）。P0-2 的「手动」
文案从「P1 后=钉住」升级为实际语义。

## Alternatives

- **夜间 sweep（方案原三调用点之一）**：心跳已覆盖新建/model 变更/未归属
  全部写入路径，apply 已覆盖规则变更存量重算；页面规则表（P0-4）实时暴露
  漂移。sweep 本轮不做，note 记录待 P1-B 评估。
- **新表用 project_key 而非 project_id**：外键用 id（F2 内部口径），与
  device.project_id 一致，减少一层解析。
- **迁移期 match_models 双写**：读侧派生 + 写侧停写即够——双写徒增不一致
  面，P1-B 直接 drop 列。

## Verification

- `test_project_attribution.py` 9 个（resolve 命中/未命中/空/inactive、
  唯一索引 IntegrityError、apply 应用/无变更/pinned/未命中保持）
- `test_heartbeat.py` 4 个（新设备自动归属 / model 变更重解析 / 补规则后
  下一次心跳归属 / pinned 不被覆盖）
- `test_project_routes.py`：map/apply 断言切规则表 + bulk 钉住幂等更新
- `test_devices.py`：四态断言含 pinned
- 107 passed（4 文件）+ ruff 全过 + 前端 tsc / vitest 40+ / eslint 全过
- worktree 隔离开发（`/tmp/p1-rule`，含 node_modules symlink），规避共享
  工作区编辑丢失

## Revisit

P1-B（下一段）：drop `match_models` 列、drop `test_project.platform`（改
派生）、form_factor 收敛 enum、Plan project+specialty 双必填+「通用」哨兵+
存量回填。夜间 sweep 在 P1-B 一并评估。
