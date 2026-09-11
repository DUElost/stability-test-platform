---
name: script-version-lifecycle
description: Agent 脚本版本的「新建」与「退役」SOP。触发时机：修改或新增刷机/设备脚本（需要新版本）、退役不再使用的脚本版本、处理 script_verify_failed 或 SCRIPT_STILL_REFERENCED 报错。
---

# Agent 脚本版本生命周期（新建 / 退役）

权威契约：`docs/development/script-versioning.md`；架构决策 ADR-0020（不可变）与
ADR-0029（每版本全量副本）。

## 执行前置检查

- [ ] 判断本次是「新建版本（改脚本行为）」还是「退役版本」——两条路径守卫不同
- [ ] 确认目标版本目录是否已发布：**已发布版本目录一律不可原地修改**
      （`script.content_sha256` 是扫描时冻结的期望值）

## A. 新建版本（改脚本行为）

1. 在 `backend/agent/scripts/<name>/v<新版本>/` 建**全量副本**（不得只拷贝差异文件）；
   入口保持首个非 `_` 前缀的可识别脚本（`.py` / `.sh`）
2. 版本 pin 走既有参数（如 `STP_FLASH_FIRMWARE_VERSION`），不要硬编码路径
3. 门禁：`python tools/dev/check-script-version-immutability.py --base origin/main`
4. 扫描注册：`POST /scripts/scan`——`conflicts` 非空即停，按冲突项修复后重扫

## B. 退役版本

1. 只读圈定候选：
   `python -m backend.scripts.check_unreferenced_script_versions [--json] [--name <name>]`
2. 补判运行态：`GET /api/v1/scripts/{id}/usage` 的 `run_count` / `success_rate` /
   `versions_used`——`refs == 0` 只代表无当前 Plan 引用，**不代表无历史运行**
3. 执行退役：`PUT /api/v1/scripts/{id}` 设 `is_active=false`
4. 若返回 409 `SCRIPT_STILL_REFERENCED`：先把引用的 plan_step 重指到新版本，再重试

## 后置验证

- 新建：重扫确认 `created` 命中且 `conflicts=0`；引用该版本的 Plan precheck 通过
- 退役：活动目录不再列出该版本，且**历史版本目录仍在磁盘**（`git status` 无删除）

## 踩坑守卫（负向约束）

- **退役 ≠ 删除**：删除版本目录会被 CI 拦下，并使历史 `plan_step.script.sha` 与磁盘
  永久失配——2026-07-31 的全平台派发中断即此类漂移引发
  （`docs/operations/incident-2026-07-31-script-sha-drift-dispatch-outage.md`）；
- 原地修改已发布版本只会产生 conflict、不更新数据库基线；引用它的 Plan 会在 precheck
  被 `script_verify_failed` 阻断，且 self-heal 无法修复磁盘与期望值的失配；
- `POST /scripts/scan?force_rebaseline=true` 仅是契约被外部破坏后的恢复手段
  （admin、无在跑 PlanRun 时才可用），不是日常改版路径。
