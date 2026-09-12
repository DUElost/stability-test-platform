# #830 修复：powercycle_finish v1.0.4 — stop→pull 撞重启窗口重拉

Status: implemented
Class: bug-fix

## Decision

**交付形态**：已发布的 `powercycle_finish/v1.0.3` 不可原地修改（ADR-0020），修复以
**新版本 v1.0.4**（全量副本，ADR-0029）+ 种子迁移
`x9y8z7a6b5c4_seed_powercycle_finish_v104`（注册新版本、deactivate v1.0.3）交付。

**种子治理（#942 裁决 A）**：迁移内嵌 `_raise_if_any_version_referenced`（复制自
`backend/services/script_seed_governance.py`，复制日期 2026-09-13），对「写已存在行」
与「停用 v1.0.3」前置做 plan_step 引用检查。**若现场存在引用 v1.0.3 的 plan_step，
迁移会按契约失败并给出重指指引**——这是设计行为（否则被引用版本被静默停用会让这些
plan 的 precheck 在派发时阻断，`plan_dispatcher_core.py:209`）。操作顺序：
先把引用 plan_step 重指到新版本（或等待模板/计划更新），再重跑迁移。

**代码变更**（`v1.0.4/powercycle_finish.py`）：

1. 新增 `_collect_with_retry(wait_s, attempts)`：stop 后的收取改「失败 → 等设备重新
   就绪（`_wait_device_online`：get-state==device **且** boot_completed==1）→ 重拉」，
   最多 `attempts` 次；全部失败才 raise，错误信息带**实际尝试次数**与最后一次原因。
2. 新参数（代码内默认，未改任何已发布版本的 `default_params`）：
   `collect_attempts=3`（`STP_POWER_CYCLE_COLLECT_ATTEMPTS`）、
   `collect_retry_wait_seconds=120`（`STP_POWER_CYCLE_COLLECT_RETRY_WAIT_SECONDS`）。
3. `_pull_result_file()` 失败诊断分离：设备离线 → 「设备离线（可能处重启窗口）」；
   在线且两路径均无文件 → 仍报「没有 powercycle_result.txt」。此前离线时 `ls` 返回空
   会被误报成「文件不存在/任务未真正运行」（把重试窗口误导成配置问题）。

**为什么是重试而不是前置复检**：v1.0.3 已在 stop **之前**做 boot_completed 就绪等待；
竞态窗口在 stop **之后**（~75s 重启周期，boot_completed 到下次重启只有 ~30s 稳定窗）。
收前再强制复检会给 happy path 增加无谓等待，而「失败后等就绪再试」用同一窗口覆盖
同一场景且不改成功路径时序。

**ground truth 核对**：issue OPEN ≠ 未修复——v1.0.3 由 #894 的清理完整化（commit
`c2b0b915`）交付，已含 pre-stop 就绪等待，但 stop→pull 段仍是单次 `adb pull` 失败即
raise：本次修复针对该残余缺口，未与 #894 的 prefs 验证工作重复。

**未改**：`pipeline_templates/powercycle.json`（teardown `timeout_seconds=600` 保持原值）
——最坏路径的预算分析见 Revisit。

## Alternatives

- **原地修改 v1.0.2/v1.0.3**：拒绝。违反 ADR-0020；且 2026-07-31 事故（sha 漂移导致
  全平台派发中断）即此类改动模式，DB 期望 sha 与磁盘永久失配，self-heal 不可修。
- **只在 stop 前加 boot_completed 复检、不加重试**：拒绝。竞态发生在 stop 之后，
  复检覆盖不到；且 v1.0.3 已有前置等待，重复动作不解决 post-stop 重启。
- **`adb wait-for-device` 包裹 pull**：未采纳。该命令只等设备出现在 adb 列表，**不等
  boot_completed**（v1.0.2 发现⑪的教训：boot 早期 run-as/ls 会失败）；显式重试循环
  的边界（次数/每次等待）可测、可配置。
- **无限重试 / 复用 `wait_device_online_seconds`（默认 600s）作为单次重试等待**：
  拒绝。teardown 步骤墙钟预算有限（模板 600s），单次等待 600s 会直接吃掉预算；
  120s 覆盖一个重启周期（~75s）+ boot 余量，且总开销有上界。
- **顺带修 `sleep_finish` 同族竞态**：不在本单范围（#894 观察项，未复现）；留 Revisit。

## Verification

- **红绿对照**（scratch，未提交）：同一「首次 pull 撞重启窗口后恢复」场景——
  v1.0.3 **失败**（RuntimeError 直接上抛，teardown 失败形态），v1.0.4 **通过**
  （2 次 pull、1 次重试等待）；
- `python -m pytest backend/agent/tests/test_powercycle_scripts.py -k CollectRetryV104 -q`
  → **5 passed**（瞬时恢复 / 到上限 raise 带尝试次数与原因 / 等不回早停 /
  `collect_attempts` 参数生效 / 离线与缺失诊断分离）；
- `python -m pytest backend/agent/tests/ -q`（`JWT_SECRET_KEY=ci-test-secret-key`，
  同 CI）→ **1732 passed**（158s）；
- `python tools/dev/check-script-version-immutability.py --base origin/main` → OK
  （无已发布版本被原地改动）；
- `python -m pytest tests/test_alembic_heads.py -q` → 1 passed（单一 head，新迁移
  `x9y8z7a6b5c4` 挂在 `q3r4s5t6u7v8` 之后）；
- `python scripts/run_gates.py check:quick` → 见 PR。

## Revisit

- **步骤墙钟预算**：模板 teardown `timeout_seconds=600`；最坏路径 = pre-wait（≤600，
  默认满额）+ stop（~7s）+ collect（120s + pull 尝试）。设备持续重启时可能被步骤墙钟
  截断——若现场出现「重试未走完即被 kill」，评估把模板提到 900，或把 pre-wait 与
  collect 合并为**单一总等待预算**（deadline 共享）；
- **`collect_retry_wait_seconds=120` 的标定**：取决于实际 boot 时长分布（slow boot
  场景），有现场数据后校准；
- **`sleep_finish` v1.0.2 同族窗口**：其 stop→收取若无等价重试，若现场复现同形态
  失败，按本方案出新版本（不做前瞻性改动）。
