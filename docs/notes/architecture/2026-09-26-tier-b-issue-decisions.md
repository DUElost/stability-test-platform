# 第二类待裁 issue 裁决（4 单）

Status: implemented
Class: architecture

## Decision

2026-09-26 同日第二轮收口。第一轮 11 单见 [2026-09-26-pending-issue-decisions-sweep.md](2026-09-26-pending-issue-decisions-sweep.md)。
本轮 4 单会改变产品或运行语义，先整理了一页选项对照（各方案的改动面与代价），owner 当日回复「均同意按照推荐项进行裁决」。
本次只落裁决、ADR 与文档，不改代码。代码事实均在 `main@9503a58` 上读码核对。

| issue | 裁决 | 关键依据 |
|---|---|---|
| #3092 | **只读回退（方案 C）+ 残余情况告警（方案 B 的可见性部分）**：锁文件以读写方式打开失败时，改为只读打开同一文件再 `flock`——拿到锁则守卫照常生效（只是不写 pid），被占用照常 fail-fast 退出；只有连只读打开都失败（如 `0600 root`）时才降级启动，并把「守卫已降级」上报为心跳字段 + 指标 + 告警。不采纳「拒绝启动」 | Linux `flock` 对只读 fd 同样生效（2026-09-26 实测：第二个进程被 EAGAIN 挡住）；`0644` 的 root 文件对 `android` 可读；只读打开不截断文件，附带的 symlink 截断风险随之消失；不改变启动语义 |
| #2909 | **条件现算 + 保留固定名单**，落为新 ADR-0055：schedule 分 `fixed` / `selector` 两种模式，存量全部迁为 `fixed`；`selector` 在链头触发时按条件（主机集 / 项目 / 标签）+ 固定健康门现算，结果照常固化为不可变派发快照 | schedule 保存的是某一时刻的结果而非规则，任何名单都会过期；需要固定对照组的回归由 `fixed` 承接；ADR-0020 是迁移 ADR，不承载 schedule 设备语义，故新立 ADR 而非修订 |
| #3066 | **方案 A + 安装前空间检查**：续链语义不变（中止即断链，ADR-0048 v1.1）；补链级可见性——根链下子 run 数少于预期环数时记链级事件并发 warning 告警；安装 APK 的族脚本在 `pm install` 前检查 `/data` 可用空间，不足即立即失败并写明可用与所需空间（按族发新版本）。**「中止也续链」（B）与「按中止来源区分」（C）之间暂不裁**：先只读查清 run 503 那 481 条中止的来源——来自人工则现行语义成立、不再改；来自系统动作才评估 C | 不管最终选 B 还是 C，可见性都是前提；空间检查与续链语义无关。481 条中止的来源需要生产库只读查询，云端会话无生产访问，查询语句已写入 #3066 |
| #2962 | **先做陈旧度派生（方案 A），阈值 7 天**：`OFFLINE` 且 `last_seen` 早于 7 天（或为空）的设备判为「陈旧」，现算不落库；设备列表默认隐藏陈旧设备（可切换显示），容量口径（#106）、链选设备（ADR-0055 健康门）默认排除，OFFLINE 计数拆分为「近期掉线」与「陈旧」。**不采纳**在 `DeviceStatus` 加 RETIRED 值（C）。设备级退役三列（B 期，ADR-0038 增补）暂缓，带复议触发器 | 202 台 OFFLINE 中 188 台超过 7 天未上报；A 不改表结构、不需要 ADR，设备回场自动恢复；C 会被心跳改写回 ONLINE/OFFLINE，与状态机冲突 |

**关于 #2962 的 B 期时机**：选项对照页对「B 期 ADR 现在起草还是延后」没有给出推荐项，本记录按保守处理为**暂缓**（见 Revisit 的触发器），如需现在启动由 owner 另行指示。

**偏离原草案 / 原文**：无。#3066 的 B/C 选择按推荐项延后到数据查清之后，不属偏离。

本次改动的文件：新增 `docs/adr/ADR-0055-schedule-device-selector.md`；`docs/adr/README.md`（主表行与 M7 看板）；本 Note。
各单的裁决同步回写到对应 issue。

## Alternatives

- **#3092 拒绝启动（A）**：残留一个 root 锁文件就会让整机 Agent 起不来，systemd 重启次数打满后需人工上机处理，代价高于它防的风险。
- **#3092 只做告警（B）**：双实例仍可发生；只读回退能在绝大多数情况下让守卫继续生效，告警只留给残余情况。
- **#2909 名单 + 定期重算（B）**：仍要定义现算判据，又多一个无人确认就改名单的写回任务。
- **#2909 修订 ADR-0020**：ADR-0020 是 Plan-Step 一次性迁移 ADR，对 schedule 只规定「只触发 Plan」，设备选择语义不在其中；塞进去会让迁移 ADR 承载现行调度语义。
- **#3066 直接选 C**：在不知道 481 条中止来源的情况下改续链语义，可能修的是一个不存在的问题。
- **#2962 直接做 B 期**：83 台超过 30 天的设备需要逐台人工确权，且要做 14 面收口；A 先落地后，是否仍需要账面终态会更清楚。

## Verification

- 读码锚点（`main@9503a58`）：`backend/agent/startup_guards.py:149-159`、`backend/agent/install_agent.sh:245`、
  `backend/models/schedule.py`、`backend/scheduler/cron_scheduler.py:125`、`backend/services/plan_chain_trigger.py:47/:156`、
  `backend/services/plan_run_aggregation.py:43-57`、`backend/services/plan_run_abort.py`（`record_audit` 的 `resource_type="plan_run"`）、
  `backend/models/enums.py:46`、`backend/api/routes/heartbeat.py:139-165`。
- 本机实测（2026-09-26）：对只读打开的文件加 `LOCK_EX|LOCK_NB` 成功，第二个进程同样加锁得到 EAGAIN。
- 在飞冲突检查：`ai_work.py declare` 以 4 个 `--issue` 登记，查重无冲突。
- 门禁：`check_governance_surface.py --check` 与 `tests/test_adr_index_status_2989.py`，结果见 PR 描述。

## Revisit

- #3066：run 503 中止来源查清后，在 B 与 C 之间裁决；若来源为人工，关闭该子问题。
- #2962 B 期：A 落地后仍存在需要人工确权的报废设备，或 #106 容量验收需要可审计的账面终态时，起草 ADR-0038 增补。
- #2962 阈值：A 上线满 30 天后按「陈旧后又回场」的设备比例回看 7 天是否合适。
- ADR-0055 自带复议触发器（现算集合大幅波动导致回归结论不可解释，或需要按型号配额选样）。
