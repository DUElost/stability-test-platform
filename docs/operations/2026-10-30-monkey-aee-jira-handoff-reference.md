# Monkey AEE 到 Jira 的交接参考（2026-10-30 三机型实战）

> **最后更新**：2026-10-30
> **定位**：经验参考与建设输入，不是新契约。日志链术语以
> [`2026-log-chain-global-semantics.md`](../design/2026-log-chain-global-semantics.md) §4 为准；
> 存储角色以 [`2026-storage-roles-and-aliases.md`](../design/2026-storage-roles-and-aliases.md) 为准。
> **范围**：专项（Monkey 等长稳）结束后，从归档扫描产物到 Jira 第一阶段输入、dry-run、
> 正式提交与回归的闭合。本文记录 2026-10-30 对 NX2 / NX3 / NX3Carrier 三个机型的实际做法、
> 计数口径、缺口与建设建议；其中的 Jira 提交段未在本轮实际执行。

## 1. 目的

专项结束后，测试结论不只是「run 成功或失败」，还要能把问题证据交给 Jira 流程。当前链路是：

```text
PlanRun 终态
  → 归档扫描（Agent）→ 控制面 merge（dedup/）
  → 报告行映射回事件目录
  → 证据导出到中心存储共享根
  → 厂商工具生成表单 + 回填计数
  → Tinno_Jira_Tool 第一阶段输入
  → dry-run → 真实提交 → 回归
```

本文把这条链上**已被实战验证的做法**与**尚未机制化的部分**分开写清楚，供后续把
「设备日志流转 + Jira 提交」做成平台能力时对照。

## 2. 适用条件与非目标

同时满足以下条件时适用本参考：

- PlanRun 已进入终态（`SUCCESS` 或 `FAILED`/中止），设备租约已释放；
- 同一 build（产品 + 版本）存在一个或多个 run，需要合成一份 Jira 输入；
- 需要把表单中每个问题的代表性事件目录放到中心存储共享根，供厂商工具消费。

非目标：

- 本文不定义采集、上送、主机汇总、控制面 merge 的协议；这些以
  [`2026-device-log-chain-contract.md`](../design/2026-device-log-chain-contract.md) 与
  [`2026-scan-upload-merge-contract.md`](../design/2026-scan-upload-merge-contract.md) 为准。
- 本文不替代 ADR-0053 的存储演进，也不定义 Jira 服务端 API。
- 本文不承诺「一键自动化」；第 4 节的步骤在当前状态下大量依赖人工判断与临时脚本。

## 3. 阶段与产物

| 阶段 | 输入 | 主要动作 | 产物 | 核对点 |
|---|---|---|---|---|
| 0 批次确认 | PlanRun 列表 | 确认 build、设备集、run 集合、终态原因 | 批次清单 | run 状态、abort 审计、设备数 |
| 1 扫描完整性 | `dedup/status` 的 `archive` 块 | 核对 host 与单位产物 | 每 host 的 `scan_result_xls` | `units_satisfied == units_expected` |
| 2 控制面 merge | 同一轮次的扫描产物 | 对 FAILED run 手动 merge | `dedup/<run>/merge/mtk/` | `Result_MergeFiles.xls` 存在且非空 |
| 3 行→事件映射 | merge 报告 + `device_log_event` | 把每行的 `Path` 落到真实事件目录 | 事件目录清单 + 缺失清单 | 每行至少一个目录，缺失可解释 |
| 4 证据导出 | 事件目录 | 按 build 目录层级复制到共享根 | `<product>_<build>_<日期>_MonkeyAEEinfo/` | 文件数与字节数一致 |
| 5 表单与计数 | 共享目录 | 厂商工具生成表单；回填真实计数 | `Result_None_None_MonkeyAEE_SH_*.xls` | 行数、Count、DeviceCount、备份 |
| 6 Jira 提交 | 三份表单 | 第一阶段输入 → dry-run → 正式提交 → 回归 | Jira 单据与回归记录 | 未在本轮执行 |

## 4. 执行要点

适用条件：一个 build 的多个 run 已终态，且 Owner 已批准向中心存储写入。

> 警告：本流程会写中心存储共享根，并可能覆盖同名表单。执行者必须先确认目标目录、
> 目标 build 与写入范围；执行者不得在未备份的情况下覆盖已有表单或事件目录。写错目录会污染
> 下游 Jira 输入，且难以从目录本身区分来源。

按顺序执行以下步骤。

### 4.1 阶段 0：确认批次

1. 查询计划下的全部 run，记录 id、状态、起止时间、设备数、build。
   预期：同一 build 的 run 集合完整，没有遗漏的补跑或续跑。
2. 如果 run 为 `FAILED` 或中止，读取审计记录中的中止人、时间、原因。
   预期：能解释「为什么没有自动 merge」，而不是把它当成设备或脚本故障。
3. 如果同一 build 已经存在历史共享目录，确定本次是「并入既有目录」还是「新建目录」。
   预期：一个 build 只对应一个 `<product>_<build>_<首轮日期>_MonkeyAEEinfo` 目录。

### 4.2 阶段 1：核对归档扫描完整性

1. 读取 `GET /api/v1/plan-runs/{id}/dedup/status` 的 `archive` 块。
   预期：`units_satisfied` 等于 `units_expected`，且 `hosts_with_artifacts` 等于 `hosts_expected`。
2. 如果单位数不足，先判断是「host 未完成」还是「产物迟到未登记」。
   查法：对比 `dedup/<run>/mtk/` 的磁盘文件与已登记的 `scan_result_xls` 记录。
3. 如果磁盘有产物但未登记，触发一次新的归档扫描，不要在缺 host 的情况下直接 merge。
   命令：`POST /api/v1/plan-runs/{id}/dedup/scan?is_final=true`。
   预期：任务结束后 `hosts=N/N`、`units=N/N`，且所有产物属于同一个 `scan_round_id`。

扫描链有三层时限，任一层过短都会让大输入 host 的产物无法登记：

| 层 | 位置 | 现值 | 说明 |
|---|---|---|---|
| Agent 扫描子进程 | `backend/agent/scan_runner.py` `_SCAN_SUBPROCESS_TIMEOUT` | 1800s | 大输入 host 在 600s 曾被连杀两轮（#3834 / PR #3835） |
| 控制面 `scan_task` 的 SAQ 超时 | `backend/services/dedup_scan.py` `_SCAN_TASK_SAQ_TIMEOUT` | 2400s | Owner 必须保证该值覆盖轮询预算加调度余量 |
| 控制面轮询预算 | 站点 env `STP_SCAN_POLL_MAX_WAIT` | 站点设为 1800s（默认 300s） | 轮询结束后不再登记迟到产物 |

### 4.3 阶段 2：控制面 merge

1. 读取 `dedup/status` 的 `run_context.merge_platforms`。
   预期：自动链跑完的 run 为 `mtk: ok`；`FAILED` run 常见 `mtk: skipped_failed`。
2. 如果 merge 被跳过，对该 run 手动触发 merge。
   命令：`POST /api/v1/plan-runs/{id}/dedup/merge`。
   预期：返回 `status=ok`、`scan_round_id` 与最近一轮扫描一致，且 `dedup/<run>/merge/<platform>/` 出现 `Result_MergeFiles.xls`、`Result_MergeFiles_org.xls`、`statistics.txt`。
3. 如果该 run 有多轮扫描，先确认最新轮次覆盖全部 host。
   预期：手动 merge 只取最新 `scan_round_id`；跨轮次直接合并会丢掉上一轮 host 的产物。
4. 如果最新轮次不完整，回到阶段 1 重扫，不要用手工拼接多轮产物替代。

### 4.4 阶段 3：把表单行映射回事件目录

1. 逐行读取 merge 报告的 `Path`、`ExpTime`、`Detail` 中的 `Device_id`。
2. 按以下顺序解析真实事件目录：
   1. `Path` 本身存在时，直接使用；
   2. `Path` 是旧格式（`<数字>_db.XX.TYPE`）或已失效时，用 `device_log_event` 的
      `remote_path` / `local_path` 按 basename 匹配；
   3. 仍无法匹配时，用 `(Device_id, ExpTime, 事件类型)` 匹配事件目录的
      `ZZ_INTERNAL` 时间；
   4. 中心存储没有时，到对应 Agent host 的 `aee_events` 事件目录中查找。
   预期：每行至少得到一个可读目录；同一事件的多个路径副本只保留一份。
3. 记录无法定位的事件（run、问题、串号、时间、类型），随表单一起交付。
   预期：缺失清单可解释、可复核，而不是静默丢行。

> 警告：`Path` 可能是旧格式 ID，或指向另一个 run 的目录（例如某 run 的扫描复用了
> 上一轮落盘）。执行者不得把 `Path` 当作字符串直接拼接或直接 `cp`，必须先确认
> 目录存在且归属设备一致。

### 4.5 阶段 4：向中心存储导出证据

1. 确定目标目录名：`<product>_<build>_<首轮日期>_MonkeyAEEinfo`。
   预期：同一 build 的多轮 run 共用一个目录；不同 build 不混放。
2. 按 `<串号>/aee_exp/<事件目录>/` 建立层级，只复制事件目录内容。
   预期：与既有 535 目录、同 build 历史目录的层级一致。
3. 目标事件目录已存在且非空时，先核对文件数与字节数；一致则跳过，不一致时先报告差异。
   预期：不覆盖既有证据，不产生半份目录。
4. 复制完成后逐目录核对文件数与字节数。
   预期：全部一致；执行者必须重传不一致的目录，或将其标记为失败。

> 警告：CIFS 共享上直接 `cp` 覆盖同名文件会因权限报错，`cp -a` 也可能失败。
> 执行者应使用 `cp -r`；确需替换文件时，先把旧文件改名，再复制新文件，最后删除旧文件。

### 4.6 阶段 5：生成表单并回填计数

1. 用厂商工具扫描共享目录，生成
   `Result_None_None_MonkeyAEE_SH_<yyyymmdd_hhmmss>.xls` 及其 `_org.xls`、`_summary.json`。
   预期：行数与目录中的问题数一致；`_org.xls` 的行可由目录中的事件逐一解释。
2. 计算真实计数，再写回表单：
   - `Count`：按 `(DeviceId, ExpTime, CausedBy)` 去重后的唯一事件数；
   - `DeviceCount`：该问题涉及的不同 `DeviceId` 数。
   预期：同一问题在两轮 run 中的重复抓取只计一次；跨 run 的设备取并集。
3. 对 `SWT` 等 `CausedBy` 含随机串的异常，按 `(ExpClass, ExpType, Package)` 归并。
   预期：不会把一个 SWT 问题拆成多条。
4. 覆盖共享表单前，先在同目录保留原件备份。
   预期：备份文件可读、md5 与原文件一致；新文件 md5 与本地回填结果一致。

> 警告：merge 报告与厂商表单里的原始 `Count` 是**行级出现次数**，不是唯一异常数，
> 也不是 Web 界面的「异常总数」。执行者不得把原始 `Count` 直接当异常总数使用；
> 执行者必须在交付前写明本次采用的口径。

### 4.7 阶段 6：Jira 第一阶段与提交（本轮未执行）

以下步骤是建议范式，2026-10-30 本轮只完成到表单与日志导出，未实际提交。

1. 把三份表单作为
   [Tinno_Jira_Tool_20260520](https://github.com/DUElost/automation-toolkit/tree/main/python-tools/stability_Jira-Automation/Tinno_Jira_Tool_20260520)
   的第一阶段输入。
2. 先执行 dry-run，输出「将新建 / 将更新 / 已存在」三类清单。
   预期：清单可逐条对照表单行与历史 Jira 单据，不产生真实写入。
3. 复核 dry-run 差异；差异未解释清楚时停止，执行者不得进入真实提交。
4. 执行真实提交，记录每个问题的 Jira key、提交时间、操作者与请求摘要。
   预期：同一问题指纹只对应一个 key；重复执行不产生重复单据。
5. 执行问题回归，把回归结果回写到同一问题台账。
   预期：提交与回归可追溯到 run、表单行、事件目录。

## 5. 三机型实战记录

三份最终表单均位于中心存储共享根，目录层级为 `<product>_<build>_<日期>_MonkeyAEEinfo`。

| 机型 | 表单 | 来源 run | 覆盖 | Count / DeviceCount |
|---|---|---|---|---:|
| NX2 | `MLD-NX2_16_261020V38_1021_MonkeyAEEinfo/Result_None_None_MonkeyAEE_SH_20261030_112728.xls` | 1096、1124 | 69 台 / 5 host | 2151 / 339 |
| NX3 | `MLD-NX3_16_261020V38_1021_MonkeyAEEinfo/Result_None_None_MonkeyAEE_SH_20261030_161620.xls` | 1079、1126 | 6 台 / 1 host | 17 / 15 |
| NX3Carrier | `MLD-NX3Carrier_16_261020V38_1023_MonkeyAEEinfo/Result_None_None_MonkeyAEE_SH_20261030_164913.xls` | 1094、1097、1125 | 23 台 / 2 host | 399 / 137 |

关键事实：

- **NX2**：两个 run 均为中止后的 `FAILED`，merge 被自动跳过；1124 在修复扫描超时后重扫
  5/5，1096 手动补 merge。1124 的事件先放入 `_1027_` 目录，后按要求并入 `_1021_`；
  1096 的 16 个差集问题补入同一目录。
- **NX3**：1079 与 1126 同 build；15 个事件目录来自 1126 报表，另补 1 个 1079 侧的第二台
  设备事件，共 16 个事件、4 个串号、1.8G。
- **NX3Carrier**：1125 首轮只登记 1/2 host，重扫后 2/2；1094 的 `Path` 大多指向
  `devices/1091`（复用上一轮落盘）。三个 run 合并后 39 个问题、53 个事件目录、7.0G；
  1094 的 10 个旧 build 事件并入 `_261013V21_1013_` 目录。
- **缺失**：1097 与 1094 各有 1 个 NE 事件在中心存储和两台 Agent host 均不存在，
  已在交付说明中单列。

## 6. 已知缺口

本轮只做到「最低可交付」，以下缺口会直接变成下一次的人工成本或错误来源：

1. **证据只有 dblog**：放入共享的主要是 dblog；mobilelog、bugreport 的覆盖不齐，
   厂商工具后续需要的材料没有保证。
2. **全链路手工**：行映射、目录复制、Agent 拉取、计数回填都是临时脚本，没有版本、
   没有测试、没有幂等保证。
3. **没有 manifest**：共享目录、报告行、run、表单之间没有机器可读关联；无法从目录
   反查「这份表单用了哪些事件、哪些缺失」。
4. **没有校验账本**：拷贝后只做现场文件数/字节比对，没有 sha256 清单与操作留痕。
5. **命名靠人**：`<首轮日期>`、product 映射、是否并入既有目录都靠执行者判断；
   NX2 曾先建 `_1027_` 再并入 `_1021_`。
6. **扫描与合并的迟到产物**：1125 的 1/2 host 是靠人发现后才重扫；平台没有
   「单位不足禁止 merge」的硬门禁。
7. **计数口径多套**：报表行 `Count`、唯一事件数、Web runtime 数三者不同；
   表单只承载一套，交接时容易误读。
8. **baseline 未拆分**：运行前遗留事件混入 `Count`，与「本次新增」无法在同一张表里区分。
9. **缺失事件无闭环**：只能事后人工发现，没有自动补齐、阈值门禁或缺失清单模板。
10. **共享写权限弱**：使用共享账号挂载，无 ACL、无审计、无并发互斥；CIFS 覆盖还需
    先改名再复制。
11. **磁盘与保留冲突**：单个 monkey run 的原始事件可达 200G 级；保留清理实际停用，
    「只删 raw、保留报告」没有平台入口。
12. **Jira 段未验证**：dry-run、真实提交、回归均未在本轮执行；提交幂等、结果未知时
    的对账、问题指纹与 key 的映射都没有落地。
13. **厂商工具是黑盒**：同一目录重复生成是否稳定、退出码与失败语义都未验证。
14. **无监控告警**：导出中断、目录半份、表单未生成、回填失败都不会自动报警。

## 7. 机制建设建议

按「先让每次交付可复核，再谈自动化」的顺序建设。

| 优先级 | 机制 | 解决的问题 | 最小实现 |
|---|---|---|---|
| P0 | 导出 manifest 与校验账本 | 目录与 run/表单无关联、无法复核 | 每次导出写 `manifest.json`：run 集合、build、目录、表单、逐问题事件清单、缺失清单、sha256、操作者与时间 |
| P0 | 控制面导出 API | 手工 cp/tar、无幂等、无权限边界 | `POST /plan-runs/export`：入参 run 集合与目标目录，服务端完成映射、复制、校验、断点续跑 |
| P0 | 统一计数服务 | 三套 Count 口径混用 | 以 `(DeviceId, ExpTime, CausedBy)` 为唯一事件定义，同时输出 raw / unique / baseline 三列 |
| P0 | 交接前置门禁 | 1/2 host、缺失事件、半份目录流入 Jira | 扫描单位、merge 轮次、映射覆盖率、缺失阈值、磁盘余量、共享写测试全部通过才允许导出 |
| P1 | 事件身份与跨 run 去重 | 同一事件多路径、多轮重复抓取 | 事件键 = 串号 + 事件时间 + 类型 + 内容摘要；同键只保留一份并记录来源 run |
| P1 | 缺失事件工作流 | 只能人工发现 | 自动生成缺失清单；可一键从 Agent host 补拉；超过阈值阻断交接 |
| P1 | Jira 幂等与对账 | 重复提交、结果未知 | 问题指纹 → Jira key 映射表；提交带幂等键；超时先对账再重试 |
| P2 | 保留策略拆分 | raw 占盘与报告保留冲突 | 原始事件按天数清理，报告与 manifest 长期保留；清理动作留审计 |
| P2 | 权限、审计与并发锁 | 共享账号、无留痕 | 每个 build 目录一把锁；写操作记录操作者、来源 run、文件清单 |
| P2 | 监控与度量 | 失败静默 | 导出/表单/回填/提交各阶段成功率、耗时、缺失数、校验失败数进监控 |

## 8. 度量与验收

| 度量 | 目标 | 数据来源 |
|---|---|---|
| 扫描完整率 | 100% host、100% 单位 | `dedup/status.archive` |
| merge 覆盖率 | 所有目标 run 均有 `merge_result_xls` | `plan_run_artifact` |
| 行映射覆盖率 | ≥ 99%，缺失逐条有解释 | 映射清单 + 缺失清单 |
| 导出校验一致率 | 100% 目录文件数与字节数一致 | manifest + 校验账本 |
| 表单计数可复核 | 每行计数可由 manifest 重算 | manifest + 表单 |
| dry-run 差异 | 100% 经人工复核 | dry-run 清单 |
| 重复提交 | 0 | Jira key 映射表 |
| 端到端时长 | run 终态到表单就绪 ≤ 1 个工作日 | 操作记录 |

## 9. 参考资料

- 设备日志链路契约：[`2026-device-log-chain-contract.md`](../design/2026-device-log-chain-contract.md)
- 日志链全局语义与易混概念：[`2026-log-chain-global-semantics.md`](../design/2026-log-chain-global-semantics.md)
- Scan / Upload / Merge 跨进程契约：[`2026-scan-upload-merge-contract.md`](../design/2026-scan-upload-merge-contract.md)
- 存储角色与别称：[`2026-storage-roles-and-aliases.md`](../design/2026-storage-roles-and-aliases.md)
- 设备日志领域重构规划（#3800）：<https://github.com/DUElost/stability-test-platform/issues/3800>
- Jira 自动化工具：<https://github.com/DUElost/automation-toolkit/tree/main/python-tools/stability_Jira-Automation/Tinno_Jira_Tool_20260520>

