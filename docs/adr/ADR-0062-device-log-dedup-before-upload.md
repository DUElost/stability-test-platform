# ADR-0062：设备日志先汇总去重、再按问题上传代表证据

- 状态：**Proposed** v0.3（2026-10-29 按独立复核第 1 轮 R1–R7 修订；§7.2 有 6 项待 Owner 裁定）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-10-29
- 决策者：Owner
- 标签：设备日志, 中心存储, 汇总去重, 容量
- 归属域：semantic-ownership center-storage-model
- 关联：[#3800](https://github.com/DUElost/stability-test-platform/issues/3800)（B8 载体）、
  [ADR-0012](./ADR-0012-post-completion-pipeline-jira-automation.md)、
  [ADR-0025 D4](./ADR-0025-phase4-architecture-alignment.md)、
  [ADR-0028](./ADR-0028-device-log-event-and-continuous-upload.md)、
  [ADR-0032](./ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md)、
  [ADR-0053](./ADR-0053-center-storage-event-dedup.md)、
  [ADR-0056](./ADR-0056-terminal-fact-layer.md)
- 版本记录：v0.1 2026-10-29 起草。目标模型由 Owner 在 B8 规划线程中给出（三层流转与中心目录契约），
  六个待定项于同日由 Owner 裁定；规划者据此整理为决策条款，并给出与既有 ADR 的关系和迁移路径。
  v0.2 2026-10-29 记录 Owner 对 v0.1 §7 与 §4 的裁定（见 §7.1）：D4 第 3 条增加「三件必须出自同一事件」；
  D5 改为按软件版本建立多个主文件夹；同意 ADR-0053 D1–D6 改为 Superseded、D7 规则 3 修订、其余规则保留；
  #3720 纳入 B8。
  v0.3 2026-10-29 按独立复核第 1 轮（PR #3833，结论「退回规划者」）修订：§1.1 现行链路事实订正（R1）；
  §4 补 ADR-0012、ADR-0032 D1/B1/D7/D10、ADR-0028 HddSpill 的处理（R2、R4）；新增 D10 运行模式与回退、
  D11 轮次完备性、D12 消费方就绪（R3、R6）；D6 第 2 条改按 `DeviceId` 列并写明累计口径（R5）；
  各条补执行主体与强度词（R7）。v0.3 新增的模型级条款均为规划者提案，以 §7.2 的 Owner 裁定为准。

## 1. 背景与证据

### 1.1 问题本质

设备日志是稳定性测试的关键产物。设备、Agent host、中心存储三层的存储上限固定，平台的核心价值是让设备日志持续、高效、便捷地流转。

现行链路（ADR-0025 D4、ADR-0028、[`06-realtime-and-background.md`](../design/06-realtime-and-background.md) §9）由三类触发驱动：
PlanRun 运行中按 `Plan.auto_archive_interval_seconds` 的增量扫描（`auto_archive_sweep`，`is_final=False`）、
PlanRun 终态扫描（`is_final=True`），以及手动触发（`POST /archive`、`POST /{run_id}/dedup/scan` 等）。每次触发后：

1. 各 Agent 执行本机扫描与主机汇总，报表按平台复制到 `dedup/{run}/{mtk|unisoc}/`（ADR-0032 B1）；
2. 控制面 `upload_task` 把报表 `Path` 列命中、且处于可标记状态（`DETECTED`、`LOCAL`、`UPLOAD_PENDING`、`UPLOADING`、`UPLOAD_FAILED`）的 DLE 行标为 `UPLOAD_PENDING`，EventUploader 把整个事件目录复制到 `devices/{run}/`；
3. 控制面 merge 按平台对各 host 报表做跨 host 去重，产出 `dedup/{run}/merge/{platform}/`。merge 与第 2 步的上传并行，不等上传完成；
4. extract 把该 run 下 `REMOTE`、`ARCHIVED`、`PRUNED` 且有 `remote_path` 的 DLE 行（`list_remote_paths_for_extract`）复制到 `jira/{run}/`，同 basename 的多个目录只复制第一个（#386），复制成功的行转为 `ARCHIVED`（ADR-0032 D10）。

去重发生在上传之后：merge 的结果只影响提单清单，不影响已经写入 `devices/` 的字节；extract 又按事件集合复制第二份。
问题身份由目录路径、文件名和厂商 xls 文本承载，下游不断从路径反推身份。

### 1.2 量化（2026-10-29，生产只读）

测量报告：[设备日志每层字节测量（Run 1096）](https://claude.ai/artifact/7BEEgmZSLWYs8dNNecea2A)、[B8 目标模型草案](https://claude.ai/artifact/AGoXKsm9gxnRDadWjKZ9RF)。
下表数字由规划者在生产只读环境测得，独立复核未复现，阶段一影子运行（§6）必须重新测量。

| 事实 | 数值 | 来源 |
|---|---|---|
| 中心存储容量 | 916 GiB，已用 71%，剩余 270 GiB | `df` |
| `devices/` / `jira/` 总量 | 419.3 GB / 83.2 GB | `du --apparent-size` |
| Run 1096 上传量 | 803 个事件目录，110.72 GB，其中 mobilelog 占 96.5% | 逐文件 stat |
| Run 1096 文件级重复内容 | 21.0 GB（19%），全部在 `devices/1096` 内部 | 逐文件 sha256 |
| 只上传每个问题一份代表证据（跨 host） | 43 个 MTK 问题，25 个目录，3.80 GB（3.4%） | 按厂商去重规则模拟，用 Run 1085 的真实 merge 校验：问题数、`Count`、`DeviceId` 列一致，字节差 0.002% |
| 只做主机汇总、不跨 host | 84 个问题，46 个目录，7.09 GB（6.4%） | 同上 |

结论：决定字节量的是「上传什么」，不是「上传后是否按内容去重」。ADR-0053 的内容寻址在 Run 1096 内最多省 19%；把顺序改为先去重后上传，可以把同一个 run 降到 3.4%。

问题数与 `Count` 一致只说明报表层等价。问题与事件的对应、代表证据是否三件齐全、提单输入与长期记录是否等价，
必须在阶段一影子运行中逐项对拍（§6 第 2 条）。

### 1.3 相关现存缺陷

- [#3829](https://github.com/DUElost/stability-test-platform/issues/3829)：DLE 状态补丁把 `size_bytes` 清成 NULL，平台没有字节台账。
- [#3830](https://github.com/DUElost/stability-test-platform/issues/3830)：展锐报表 `Path` 与事件目录对不上，Run 1096 展锐事件没有进入中心存储。
- [#3831](https://github.com/DUElost/stability-test-platform/issues/3831)：扫描报表的项目名、软件版本为 None。
- [#3832](https://github.com/DUElost/stability-test-platform/issues/3832)：Run 1085 有 180 个事件被重复上传（23%）。

## 2. 目标模型

```text
设备 ──导出──> Agent host（本地保存全部事件）
                  │ 每轮：本机扫描 + 主机汇总 → 主机报表
                  ▼
               控制面（按 PlanRun 收齐各 host 报表 → 控制面 merge → 问题清单 + 累计统计入库）
                  │ 回传：每个新问题由哪台 host 的哪个事件作代表
                  ▼
               Agent host（只上传被选中的代表事件）──> 中心存储（每个问题一个目录）
```

运行中每轮重复上述流程，PlanRun 进入终态时再做最后一轮。已上传过的问题在后续轮次只更新累计统计。

## 3. 决策

本节条款只约束运行模式为「先去重」的 PlanRun 平台分区（D10）。运行模式为「现行」的分区继续按 ADR-0025 D4、ADR-0028、ADR-0032 执行。

### D1 先汇总去重，再上传

1. Agent 不得在控制面回传选择结果之前，把事件证据上传到中心存储。D1 第 3 条是唯一例外。
2. Agent 只能把控制面选定的代表证据写入中心存储的证据区。主机报表、控制面 merge 报表和累计统计导出文件不受此限。
3. （提案，§7.2 第 4 项）Agent 本地磁盘达到 HddSpill 阈值时，Agent 可以继续用 HddSpill 把事件强制上传到 `devices/{run}/`，以保证磁盘安全。
   控制面必须把溢出事件计入该 PlanRun 的溢出台账并告警；溢出事件被选为代表时，控制面必须让 Agent 或控制面从溢出副本写入 D5 目录，不得要求设备重新导出。

### D2 三层职责

1. Agent 必须按 PlanRun 执行本机扫描与主机汇总（MTK 为 `start_log_scan -m 0` 加 `-dedup_org`），并把主机报表上送控制面。
2. 控制面必须按 PlanRun 与平台收齐本轮期望的主机报表（D11）后执行控制面 merge，维护问题清单与累计统计，并为每个新问题选定代表事件。
3. 控制面必须把选定结果回传给持有该代表事件的 Agent。
4. Agent 必须只上传被选中的代表事件，并按 D5 的目录契约写入中心存储。上送、校验与回执沿用 EventUploader 现有链路；Agent 在中心确认前不得删除本地副本（#1083）。
5. 问题身份、问题与事件的对应、代表事件的选择都以控制面数据库为准。任何组件不得从中心目录名、报表文件名或 xls 文本反推这些身份。
6. Agent 上送的每份主机报表必须绑定 PlanRun、host、平台与轮次。控制面不得把无法证明属于本 PlanRun、本平台的材料作为输入（#3720）；迟到材料按 D11 第 4 条处理。

### D3 周期

1. 控制面必须在 PlanRun 运行中按固定间隔触发一轮 D2 流程，默认间隔 6 小时。间隔必须是可配置项。
2. 控制面必须在 PlanRun 进入终态（`SUCCESS`、`PARTIAL_SUCCESS`、`FAILED`）后再触发最后一轮。
3. 控制面必须以增量方式执行每一轮：已选定代表的问题只更新累计统计，控制面不得重新选择代表，Agent 不得重新上传。

### D4 代表证据

1. 控制面必须在选择时已知的出现中，选择该问题最早出现、且 dblog、mobilelog、bugreport 三件齐全的那个事件作代表。
2. 同一问题在其他设备或后续轮次再次出现时，控制面只能更新累计统计，不得要求补传证据。
3. dblog、mobilelog、bugreport 三件必须出自同一个事件，即同一台设备的同一次报错。控制面不得用不同设备的同一问题、或同一设备不同时间点的同一问题拼凑出三件齐全。
4. 本轮没有三件齐全的事件时，控制面不得为该问题选代表，留到后续轮次再选。最后一轮关闭（D11 第 3 条）时仍没有齐全事件，控制面必须选最早出现的事件，并把该问题标为「证据不全」。

### D5 中心存储目录契约

1. Agent 必须把代表证据写入以下结构：

   ```text
   {证据区根}/
   └── {主PlanRun编号}_{项目名}_{软件版本缩写}_{测试开始时间戳}_MonkeyAEEinfo/   # 每个软件版本一个
       └── {SN}/
           └── {事件目录名}/        # 设备上的原名，形如 {时间戳}_db.{XX}
               ├── dblog
               ├── mobilelog
               ├── bugreport
               └── 累计统计导出文件
   ```

   证据区根是中心存储下新增的一个顶层目录，与 `devices/`、`jira/` 并列，名称由实施方案确定并登记到 `storage_families`。
2. 主 PlanRun 是链式执行的根 run（`plan_run.root_plan_run_id`），没有链时是 PlanRun 本身。
3. 同一 PlanRun 内设备的软件版本不一致时，Agent 必须按软件版本建立多个主文件夹，每台设备的代表证据写入其软件版本对应的主文件夹。
4. 实施者必须按离线工具
   [stability_Offline-MonkeyTestTool](https://github.com/DUElost/automation-toolkit/tree/main/python-tools/stability_Offline-MonkeyTestTool)
   的 `_get_aee_log_folder_name` 规则生成 `{项目名}_{软件版本缩写}_{测试开始时间戳}`。该规则从设备属性
   `ro.product.name`、`ro.build.display.id`、`ro.build.version.incremental`、`ro.build.version.release` 推导，含按产品分支的特例。
   实施者必须把它实现为按项目可扩展、带版本的规则；新增项目时只能增加规则，不得改已有规则。
5. 一个事件目录可能包含多个问题。同一事件目录被多个问题选为代表时，Agent 只能上传一次，累计统计导出文件必须列出该目录承载的全部问题。
6. 累计统计导出文件是控制面数据库的副本，供提单与人工查看；权威在控制面数据库（D6）。
7. 主文件夹归属主 PlanRun 的整条链。任何组件不得按单个 PlanRun 删除主文件夹或其中的事件目录；清理单位是整条链，并按 D8 第 2 条取得 Owner 确认。

### D6 问题与累计统计

1. 问题实体沿用 ADR-0056 D2 已接受的 `issue_signature` 与 `issue_occurrence`。实施者必须新增「PlanRun × 问题」一级的汇总与代表选择记录，表名按单数命名。
2. 累计统计至少包括出现次数与机器数，取自控制面 merge 报表的 `Count` 列与 `DeviceId` 列（ADR-0032 B3：MTK 输出表头为 `DeviceId`），控制面必须按
   [`2026-scan-upload-merge-contract.md`](../design/2026-scan-upload-merge-contract.md) 的表头契约按列名消费。
3. 每轮的主机报表覆盖该 host 在本 PlanRun 内仍保留的全部事件，控制面 merge 的输入是每个 (host, 平台) 的最新一份报表。
   控制面必须用每轮 merge 的结果覆盖累计统计，不得把各轮结果相加。
4. 本条修订 ADR-0053 D7 规则 3：同一 PlanRun 内的签名级去重与累计计数由 STP 负责。跨 PlanRun 是否并入既有 JIRA 单，仍归提单工具与 JIRA。
5. ADR-0053 D7 规则 1、2、4、5（同一发生跨 run 不重复提单、baseline 默认不进提单输入、身份无法证明时 fail-open、是否已提单只查长期记录）继续有效。

### D7 Agent 本地保留

1. Agent 必须把未被选中的事件保留到该 PlanRun 最后一轮关闭（D11 第 3 条）后再过 N 天，以便补取。N 天从 Agent 收到关闭通知时起算；离线未收到通知的 Agent 必须继续保留。
2. N 由 Owner 在 Agent 本地缓冲测量完成后裁定；裁定前，Agent 不得因本 ADR 新增删除路径。

### D8 平台范围与旧数据

1. 本 ADR 只覆盖 MTK。展锐分区沿用现行链路，主文件夹名预留为 `…_MonkeyYloginfo`，由后续批次合入。
2. 旧数据不迁移。运行模式为「现行」的 run 继续留在 `devices/` 与 `jira/`；确认已提单后按 run 归档或删除，删除前必须单独取得 Owner 确认。

### D9 提单工具与报表格式

1. 提单工具必须改为读取 D5 的目录与累计统计导出文件。提单工具与汇总去重工具均为自研，由提单工具一侧做兼容。
2. 提单工具不得在 D5 目录内原地写入；需要工作区时必须复制到独立工作区（沿用 ADR-0053 D5 的隔离原则）。
3. 实施者不得改变主机报表与控制面 merge 报表的内部格式。需要新增工具参数时，实施者必须先提出并取得 Owner 同意。

### D10 运行模式、交付与回退（提案，§7.2 第 1–3 项）

1. 控制面必须为每个 (PlanRun, 平台) 记录运行模式：「现行」或「先去重」。控制面在该分区第一轮开始时按开关写入运行模式，此后不得改写。
2. 只有 MTK 分区可以使用「先去重」。混平台 PlanRun 的展锐分区必须使用「现行」，两个分区按 ADR-0032 B1 各自判定完备与交付，产品面仍合并为一张总表。
3. 「先去重」分区的交付物是 D5 目录加 JiraRun。该分区的 JiraRun 必须指向 D5 主文件夹；控制面不得为该分区执行 extract，手动 extract 接口必须对该分区返回 409。
4. 「先去重」分区的代表事件写入 D5 目录并校验通过后，控制面必须把该 DLE 行转为 `ARCHIVED`。`ARCHIVED` 的含义随之修订为「该事件已进入本分区的交付物」：「现行」分区是 `jira/{run}/`，「先去重」分区是 D5 目录。未被选中的事件保持 `LOCAL`。
5. `FAILED` 的「先去重」分区必须照常执行最后一轮与代表上传，使证据进入中心存储；控制面不得为 `FAILED` 分区自动发起 JiraRun，由人决定是否提单（沿用 ADR-0028 D2 与 #697 的人工门禁）。
6. 关闭开关只影响之后开始的分区。已按「先去重」开始的分区必须按「先去重」完成。
   某个「先去重」分区需要回退时，Owner 可以对该分区发起一次「现行补跑」：控制面对仍在 Agent 本地保留期内的事件执行现行 upload 与 extract，产物进 `devices/{run}/` 与 `jira/{run}/`，D5 目录保留不动。

### D11 轮次完备性（提案，§7.2 第 5 项）

1. 每一轮的期望集是 (host, 平台) 对，控制面必须沿用 ADR-0032 B1 的 `scan_completeness` 从 PlanRun 设备表派生。没有事件的 host 必须上送空报表或等价的「无事件」回执，计入已收齐。
2. 控制面必须为每轮记录三种结局之一：「完整」（期望集全部收齐并 merge 成功）、「部分」（等待期满仍有缺失，用已收到的报表 merge）、「失败」（merge 失败，本轮不产生选择）。
   等待期默认等于 D3 的间隔，必须可配置。「部分」与「失败」必须在 PlanRun 详情页可见并列出缺失的 (host, 平台)。
3. 最后一轮在期望集全部收齐时以「完整」关闭；最后一轮等待期满仍有缺失时，控制面必须以「部分」关闭，并向 Agent 发送关闭通知，D7 的 N 天从此起算。
4. 迟到报表（属于本 PlanRun、本平台，但晚于所属轮次的等待期）必须作为下一轮的输入。最后一轮关闭后到达的报表，控制面必须触发一次补充轮；补充轮只能为尚无代表的问题选代表，并更新累计统计。

### D12 消费方就绪（提案，§7.2 第 6 项）

1. 阶段二打开任何分区的「先去重」开关之前，实施者必须让下表所列消费方全部能处理「先去重」分区，并在阶段一用影子数据验证。

   | 消费方 | 「先去重」分区下的行为 |
   |---|---|
   | `backend/core/artifact_paths.py`（`resolve_device_event_remote_path` 只认 `devices/`） | 增加证据区根的校验路径，`devices/` 规则不变 |
   | DLE 回执白名单（`backend/services/agent_device_log_events.py`） | 接受证据区根下的 `remote_path` |
   | DLE 下载（`device_log_event_download.py`）与产物下载（`plan_run_artifact_download.py`） | 能从证据区根下载 |
   | extract（`dedup_extract.py`）、手动 extract 接口、`extract_bundle` | 不执行；手动接口返回 409（D10 第 3 条） |
   | JiraRun `storage_uri` | 指向 D5 主文件夹 |
   | `auto_archive_sweep` 归档完成判据（现为 merge + extract） | 改为最后一轮关闭且代表上传完成 |
   | 前端 `PlanRunDetailPage`、`DedupReportCard`、`LogEventsCard` | 显示运行模式、轮次结局、问题清单与证据路径 |
   | `storage_families` 与其 GC | 登记证据区根，按 D5 第 7 条以链为清理单位 |
   | 未归属事件认领（`associate_unassigned_events_to_plan_run`） | 认领后进入所属分区的期望集 |
   | `measure_center_storage` | 统计证据区根 |
   | HddSpill（`local_disk_monitor.py`） | 按 D1 第 3 条 |

2. 实施方案可以补充本表遗漏的消费方；发现遗漏的实施者必须在 PR 中登记，并在打开开关前处理完毕。

## 4. 对既有 ADR 的处理

| ADR | 处理 | 理由 |
|---|---|---|
| ADR-0053 D1–D3、D6、Phase A–D | Superseded（Owner 2026-10-29 同意；本 ADR 转 Accepted 时同步改 ADR-0053 状态并关闭 #3308） | 中心存储只存每个问题一份代表证据后，内容寻址要消除的跨 run / 跨事件副本基本不再产生；保留它的复杂度（blob、manifest、引用 GC）没有对应收益 |
| ADR-0053 D4、D5 | 提案保留（§7.2 第 6 项）：D4 的 baseline 身份前提由 D7 规则 1、2 引用；D5 的工作区隔离由本 ADR D9 第 2 条引用 | 这两条约束身份与工作区，不依赖内容寻址 |
| ADR-0053 D7 | 规则 3 按本 ADR D6 第 4 条修订，其余保留 | 见 D6 |
| ADR-0025 D4、ADR-0028 | 对「先去重」分区，触发时机、上传选择依据与落点按本 ADR 执行；HddSpill 按 D1 第 3 条保留 | 见 D1–D5 |
| ADR-0028 D2（`FAILED` 自动 merge 跳过） | 对「先去重」分区修订：merge 是选择步骤，`FAILED` 分区照常执行；自动提单仍跳过 | D10 第 5 条 |
| ADR-0032 D1、B1 | 沿用：平台分区各自输入、各自 merge、各自判定完备；本 ADR 的运行模式按分区记录 | D10 第 1、2 条 |
| ADR-0032 D7 | 同 ADR-0028 D2 | D10 第 5 条 |
| ADR-0032 D10（`ARCHIVED` = 复制进 `jira/{run}/`） | 修订：`ARCHIVED` = 进入本分区交付物；区分依据是运行模式，不是平台 | D10 第 4 条 |
| ADR-0012（extract 材料包 + JiraRun 是唯一交付路径） | 修订：「先去重」分区的唯一交付路径是 D5 目录 + JiraRun；「现行」分区不变 | D10 第 3 条 |
| ADR-0056 D2 | 沿用，作为 D6 的问题实体 | 不另造身份 |

## 5. 放弃的备选

| 备选 | 结论 | 理由 |
|---|---|---|
| 保持先上传后去重，实施 ADR-0053 内容寻址 | 否决 | Run 1096 最多省 19%，本 ADR 的顺序调整省 96.6% |
| 只做主机汇总就上传（不等控制面 merge） | 否决 | 6.4% 对 3.4%，且跨 host 的同一问题仍会重复上传；回传链路是本 ADR 的必要成本 |
| 只在 PlanRun 终态做一轮 | 否决 | 长时间运行的 PlanRun 中 Agent 本地缓冲会持续增长，且问题要等到结束才可见 |
| 中心存储只放 brief、证据全部按需拉取 | 否决 | 提单需要代表证据随时可取；Owner 选定每个问题上传一份完整证据 |
| HddSpill 在「先去重」分区停用 | 否决（提案） | 磁盘写满会丢失尚未导出的日志，代价高于溢出到中心的字节 |
| 运行中允许把分区从「先去重」切回「现行」 | 否决（提案） | 已上传的代表与现行链路的标记互相干扰，回退改用 D10 第 6 条的「现行补跑」 |

## 6. 迁移与激活（按 ADR-0058 批次 B8）

1. 前置：#3829（字节台账）与 #3831（项目名、版本来源）先按单点修复。
2. 阶段一（影子运行）：控制面按 D3 周期执行 merge 与代表选择，只写数据库，不改变上传。
   规划者或实施者必须用真实 PlanRun 对拍影子结果与现行链路的以下各项：问题数、累计统计、问题与事件的对应、代表证据三件齐全率、提单输入与应上传字节。D12 的消费方在本阶段完成。
3. 阶段二（切换上传）：Owner 确认 D12 全部就绪后，可以按分区打开「先去重」开关（D10）。关闭开关后新分区回到「现行」，已开始的分区按 D10 第 6 条处理。
4. 阶段三（提单切换）：提单工具改读 D5 目录（D9）。验收前，Owner 可以对「先去重」分区发起「现行补跑」作为对照。
5. 旧数据处理按 D8 第 2 条单独确认。

## 7. Owner 裁决记录

### 7.1 v0.2（2026-10-29）

| 项 | 裁定 |
|---|---|
| 证据不全时的兜底（D4） | 同意规划者提案；前提是三件必须出自同一台设备的同一次报错，不得跨设备或跨时间点拼凑（D4 第 3 条） |
| 同一 PlanRun 内软件版本不一致（D5） | 按软件版本建立多个主文件夹（D5 第 3 条） |
| ADR-0053 | D1–D6 与 Phase A–D 改为 Superseded，#3308 随之关闭；D7 规则 3 按 D6 第 4 条修订，规则 1、2、4、5 保留 |
| ADR-0025 / ADR-0028 | 终态 dedup 管道的触发点与上传选择依据由本 ADR 写明（§4） |
| issue 归属 | #3800 改写为 B8 批次载体；#3720 纳入 B8；#3766 随展锐后续合入；#3176、#3829 按单点处理 |

### 7.2 v0.3 待裁定（独立复核第 1 轮提出）

| # | 项 | 规划者提案 |
|---|---|---|
| 1 | 运行模式与混平台（R2） | 按 (PlanRun, 平台) 记录运行模式，首轮写入后不改；只有 MTK 可用「先去重」（D10 第 1、2 条） |
| 2 | 交付路径与 `ARCHIVED` 含义（R2） | 「先去重」分区交付 = D5 目录 + JiraRun，不再 extract；`ARCHIVED` 改为「进入本分区交付物」（D10 第 3、4 条，修订 ADR-0012 与 ADR-0032 D10） |
| 3 | `FAILED` 与回退（R2、R3） | `FAILED` 分区照常做最后一轮并上传代表，不自动提单；回退用「现行补跑」，不允许运行中切换（D10 第 5、6 条） |
| 4 | HddSpill（R4） | 保留为磁盘安全出口，溢出进 `devices/{run}/` 并告警，溢出事件可作代表（D1 第 3 条） |
| 5 | 轮次完备性（R6） | 期望集沿用 `scan_completeness`；轮次分「完整 / 部分 / 失败」；等待期默认 6 小时；最后一轮关闭后 N 天起算；迟到报表进下一轮或补充轮（D11） |
| 6 | ADR-0053 D4、D5 与消费方清单（R3、复核非阻断意见） | ADR-0053 D4、D5 不随 D1–D3 一起 Superseded，改为保留；消费方按 D12 表处理，开关前全部就绪 |

## 8. 复议触发

- 影子运行中，控制面 merge 的问题数、累计统计或问题与事件的对应与现行 merge 不一致。
- Agent 本地缓冲测量显示 D7 的保留期无法放下单个 PlanRun 的全部事件。
- HddSpill 溢出字节在单个 PlanRun 内超过代表证据字节。
- 展锐合入时，主机汇总工具的报表形态无法满足 D2 第 1 条。
