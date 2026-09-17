# 越过刷机里程碑后的失败判 PARTIAL_SUCCESS（#1591-④ 裁决落地）

Status: implemented
Class: feature

## Decision

**用户裁决（2026-09-17）：flash 成功但后续步骤失败时，run 应判 `PARTIAL_SUCCESS` 而不是
`FAILED`**（否则 31 台批次里「刷机已成功、卡在 oobe/root」的那些台会被整批 FAILED 抹平——
issue 实证：31 台批次的 flash 步 COMPLETED 31 次 / FAILED 21 次，而 14 台的失败发生在后续步骤）。

**判据（里程碑规则）**：run 的全部 FAILED job 都已越过里程碑步骤（该脚本的 step_trace
`COMPLETED`）时，整批判 `PARTIAL_SUCCESS`；否则维持原有规则（阈值 / abort 优先）。

- **里程碑 = 脚本名白名单**：`_MILESTONE_SCRIPT_NAMES = ("flash_firmware",)`。脚本名耦合在
  本仓有先例（`LEGACY_AEE_SCRIPT_NAMES` / `_WIFI_CONSUMER_SCRIPT_NAMES`），且里程碑步骤由
  **plan 快照**的 `script_name` 反查 `step_key`（不猜步骤名，`step_key` 由用户自定义）。
- **保守优先**：无 db 会话、快照里没有里程碑步骤、FAILED 计数与实际行不一致、查库异常——
  一律返回"不满足"，即维持今天的 FAILED。**判据读不到绝不放宽**。
- **abort 优先不变**（#783 裁决）：有 abort 则一律 FAILED，里程碑只放宽"自然失败"。
- **两条聚合路径同语义**：计数器（O(1)）与全量扫描都做同一判定；`db` 是关键字节参数
  （不传 = 跳过判定 = 保守），4 个生产调用点全部显式传入。

**为什么不是别的做法**：
- **调高 `failure_threshold`**：否决。那会同时把「1/31 台失败」判成 SUCCESS（更严重的掩盖），
  且阈值是计划级质量控制旋钮，不该兼职表达"刷机目标已达成"。
- **新增 `plan_step.is_milestone` 列**：否决（本单）。需要迁移 + 空库门禁 + UI，而 issue 的
  诉求集中在刷机链（脚本名白名单已能表达）；若将来出现第二个里程碑场景，再升格为显式列。

## Alternatives

- **A. 只在 job 级表达**：否决。job 状态枚举没有"部分成功"，且 issue 的诉求是 run 级判定粒度。
- **B. 用 `job.status_reason` 文本解析失败步骤**：否决。文本是给人看的（"lifecycle init
  failed: step failed in init: oobe: …"），解析它等于把日志格式变成契约。
- **C. 把里程碑判定做成"失败步骤不是第一步"**：否决。1 步计划失败会被判成部分成功（错），
  且不能区分"刷机失败"与"刷机后失败"这两类完全不同的结果。
- **D. 只在全量扫描路径实现**：否决。生产主路径是计数器路径（`total_job_count > 0`），
  只改一条会让规则在最常见的情形下静默不生效（本单的接线守卫正是防这个）。

## Verification

- **测试**（`backend/tests/services/`）：
  - 单元 6 例：越过里程碑 → `PARTIAL_SUCCESS`（全量 + 计数器两路）；任一 FAILED job 未越过
    → `FAILED`；`db=None` → 保守 `FAILED`；快照无里程碑脚本 → `FAILED`；abort → `FAILED`
    （#783 回归）；
  - **接线 2 例**：`on_job_terminal_sync` 走真实 `db_session` + 真实 StepTrace（flash
    COMPLETED / oobe FAILED）→ `PARTIAL_SUCCESS`；以及**调用点守卫**（静态）：两个生产
    文件里每个聚合调用点都必须带 `db=db`。
  - 相关套件合计 **149 passed**（聚合 shared / 终态化 / post_completion / abort 竞态 /
    aggregation endpoints / abort API）。
- **红绿差分与变异**：
  - 基线实现上 6 条新用例红（`TypeError: unexpected keyword argument 'db'`）；
  - 变异 1（里程碑判定恒定返回 False）→ 接线用例红（`status == PARTIAL_SUCCESS` 断言失败）；
  - 变异 2（某调用点漏传 `db=`）→ 调用点守卫红（`… 调用点漏传 db：…`）。
  - 首版守卫曾把 `from … import (…)` 的续行当调用点（假阳性），已修并复跑双向。
- `ruff check`、`check:quick`（10 gates）通过。
- **未做**：未对生产历史 run 回溯重判（规则只作用于新的终态判定）——如需回填口径，单独议。

## Revisit

- **里程碑白名单的扩展**：目前只有 `flash_firmware`。若"越过某步骤即视为主要目标达成"的
  场景增多（如 scan/merge 链），应升格为 `plan_step` 的显式列（迁移 + UI 勾选），而不是
  继续加脚本名。
- **未回填存量 run**：规则只影响新判定；历史 run 的 FAILED 保持原样。若运维需要按新口径
  看历史批次，需要一次只读重算（不改行）或接受差异。
- **`PARTIAL_SUCCESS` 的消费面**：`/results/risk-trend`、列表终态过滤、metrics 都已按它处理
  （一直是终态枚举的一员）；本单让它在刷机批次上**首次真正出现**，若下游有"只有 SUCCESS 才算
  通过"的隐含假设，会在这些批次上显形——观察首个刷机批次的结果。
- **流程教训（本单实际发生）**：我在共享工作区用相对路径编辑时 cwd 漂移，改动落到根检出
  后被丢弃（无一提交被污染，已核对 `git log --all -S`）。已改为「绝对路径 + 单次 `cd` 执行」；
  共享工作区的分支漂移与丢弃风险见既有记录。
