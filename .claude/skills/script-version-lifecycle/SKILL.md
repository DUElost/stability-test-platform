---
name: script-version-lifecycle
description: Agent 脚本版本的「新建」与「退役」SOP。触发时机：修改或新增刷机/设备脚本（需要新版本）、新版本合并后要把文件下发到主机、退役不再使用的脚本版本、处理 script_verify_failed 或 SCRIPT_STILL_REFERENCED 报错。
---

# Agent 脚本版本生命周期（新建 / 退役）

权威契约：`docs/development/script-versioning.md`；架构决策 ADR-0020（不可变）与
ADR-0029（每版本全量副本）。

## 执行前置检查

- [ ] 判断本次是「新建版本（改脚本行为）」还是「退役版本」——两条路径守卫不同
- [ ] 确认目标版本目录是否已发布：**已发布版本目录一律不可原地修改**
      （`script.content_sha256` 是扫描时冻结的期望值）

## A. 新建版本（改脚本行为）

1. 直接改族源码树 `backend/agent/scripts/<name>/`（ADR-0051 Phase 3 起无版本目录，每族一棵树）；
   改完 `python tools/dev/check_script_packages.py --register <name> <新版本>` 追加登记（版本号不可复用）
2. 版本 pin 走既有参数（如 `STP_FLASH_FIRMWARE_VERSION`），不要硬编码路径
3. 门禁：`python tools/dev/check_script_packages.py`（族树重建 sha 须等于最新登记，改树没 `--register` 即红）+ `python tools/dev/check_tool_manifest.py --base origin/main`（append-only）
4. 合入后发包：`python tools/dev/check_script_packages.py --publish --packages-root /mnt/stp-aee/packages`；再 `POST /scripts/scan`——`package_missing` 非空说明没发包，`conflicts` 非空即停
5. **让主机拉到新包（版本生效的最后一公里）**——ADR-0051 Phase 3 起脚本从主机本机
   `tools_cache/<name>/<version>/` 执行（`STP_SCRIPT_PACKAGES=strict`，Agent 按 `script.package_sha256`
   从站点 `packages/` 拉包并整包核验），主机上**没有**脚本目录，热更新 code 载荷也不再含 `scripts/`：
   - 第 4 步发包 + scan 后，Plan 派发前的 `verify_scripts` 会自动拉包预热；要提前核验用
     `POST /api/v1/script-presence/refresh?host_id=<id>`（走整包核验），期望 `missing=0 mismatch=0`；
   - 主机侧判据：`find /opt/stability-test-agent/tools_cache -name .stp-verified | wc -l` 含新版本，
     `journalctl -u stability-test-agent | grep -c 'script package unavailable'` 为 0；
   - **不需要**热更新 Agent 代码来分发脚本（那是 Phase 3 前的形态）。

## B. 退役版本

1. 只读圈定候选：
   `python -m backend.scripts.check_unreferenced_script_versions [--json] [--name <name>]`
2. 补判运行态：`GET /api/v1/scripts/{id}/usage` 的 `run_count` / `success_rate` /
   `versions_used`——`refs == 0` 只代表无当前 Plan 引用，**不代表无历史运行**
3. 执行退役：`PUT /api/v1/scripts/{id}` 设 `is_active=false`
4. 若返回 409 `SCRIPT_STILL_REFERENCED`：先把引用的 plan_step 重指到新版本，再重试

## 后置验证

- 新建：重扫确认 `created` 命中且 `conflicts=0`；引用该版本的 Plan precheck 通过
- 新建（**到位**，与「注册」分开看）：`GET /api/v1/script-presence/summary` 的
  `uncovered_active_versions` = 「已 active 但无任何 Plan 引用」的版本数——这些版本
  账本**不会**核验，所以 `counts.missing/mismatch = 0` 只覆盖 `full_versions`，不含它们。
  新版本在没被 Plan 引用前正落在这个集合里，其到位情况只能靠「第 5 步已跑」+
  逐台 `agent_code_sync_status=matched` 确认（明细面板里与「目标版本」并排显示）
- 退役：活动目录不再列出该版本，且**历史版本目录仍在磁盘**（`git status` 无删除）
- **第 4 道（既有 Plan 重指）**：`STP_SCRIPT_ROOT=<部署树>/backend/agent/scripts
  python -m backend.scripts.check_unreferenced_script_versions --plan-step-drift`——
  活跃 Plan 的步骤不再出现在该视图（半活跃随下个窗口清）；追平前先看 Δ 类型：
  `review_required` / `metadata_diff` 必须人工核参数/预算契约再改版本号（#3030）
- **登记表复查期**：`--plan-step-drift` 输出里的「冻结/待核登记已过期」非空 = 该冻结
  已到复评日，须重新裁决（不许沉默冻结）

## 踩坑守卫（负向约束）

- **DB `active` ≠ 主机可用（#3111）**：脚本从主机本地树执行，scan 只写注册表——新版本
  合并后不跑一次 fleet 热更新，就停在「版本已 active、Plan 一引用即在 precheck/派发
  缺文件」的空档，而 `script-presence` 的 `missing=0` 会一路保持绿（该版本无 Plan 引用
  ⇒ 不在账本全集内）。下发是新建版本的**组成部分**，不是可选项；
- **退役 ≠ 删除**：删除版本目录会被 CI 拦下，并使历史 `plan_step.script.sha` 与磁盘
  永久失配——2026-07-31 的全平台派发中断即此类漂移引发
  （`docs/operations/incident-2026-07-31-script-sha-drift-dispatch-outage.md`）；
- 原地修改已发布版本只会产生 conflict、不更新数据库基线；引用它的 Plan 会在 precheck
  被 `script_verify_failed` 阻断，且 self-heal 无法修复磁盘与期望值的失配；
- `POST /scripts/scan?force_rebaseline=true` 仅是契约被外部破坏后的恢复手段
  （admin、无在跑 PlanRun 时才可用），不是日常改版路径。
