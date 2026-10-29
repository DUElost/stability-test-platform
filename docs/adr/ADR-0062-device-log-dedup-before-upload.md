# ADR-0062：设备日志先汇总去重、再按问题上传代表证据

- 状态：**Proposed** v0.1（2026-10-29 起草，待独立复核与 Owner 裁决）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-10-29
- 决策者：Owner
- 标签：设备日志, 中心存储, 汇总去重, 容量
- 归属域：semantic-ownership center-storage-model
- 关联：[#3800](https://github.com/DUElost/stability-test-platform/issues/3800)（B8 载体）、
  [ADR-0025 D4](./ADR-0025-phase4-architecture-alignment.md)、
  [ADR-0028](./ADR-0028-device-log-event-and-continuous-upload.md)、
  [ADR-0053](./ADR-0053-center-storage-event-dedup.md)、
  [ADR-0056](./ADR-0056-terminal-fact-layer.md)
- 版本记录：v0.1 2026-10-29 起草。目标模型由 Owner 在 B8 规划线程中给出（三层流转与中心目录契约），
  六个待定项于同日由 Owner 裁定；规划者据此整理为决策条款，并给出与既有 ADR 的关系和迁移路径。

## 1. 背景与证据

### 1.1 问题本质

设备日志是稳定性测试的关键产物。设备、Agent host、中心存储三层的存储上限固定，平台的核心价值是让设备日志持续、高效、便捷地流转。

现行链路（ADR-0025 D4、ADR-0028、[`06-realtime-and-background.md`](../design/06-realtime-and-background.md) §9）在 PlanRun 进入终态后才触发：

1. 各 Agent 扫描并做主机汇总，报表复制到 `dedup/{run}/`；
2. 控制面把报表 `Path` 列引用到的事件全部标为 `UPLOAD_PENDING`（`backend/tasks/saq_tasks.py:upload_task`），EventUploader 把整个事件目录复制到 `devices/{run}/`；
3. 之后控制面 merge 才做跨 host 去重；
4. extract 再把 merge 引用到的事件复制一份到 `jira/{run}/`。

去重发生在上传之后，所以每层只减少报表行数，不减少字节。问题身份由目录路径、文件名和厂商 xls 文本承载，下游不断从路径反推身份。

### 1.2 量化（2026-10-29，生产只读）

测量报告：[设备日志每层字节测量（Run 1096）](https://claude.ai/artifact/7BEEgmZSLWYs8dNNecea2A)、[B8 目标模型草案](https://claude.ai/artifact/AGoXKsm9gxnRDadWjKZ9RF)。

| 事实 | 数值 | 来源 |
|---|---|---|
| 中心存储容量 | 916 GiB，已用 71%，剩余 270 GiB | `df` |
| `devices/` / `jira/` 总量 | 419.3 GB / 83.2 GB | `du --apparent-size` |
| Run 1096 上传量 | 803 个事件目录，110.72 GB，其中 mobilelog 占 96.5% | 逐文件 stat |
| Run 1096 文件级重复内容 | 21.0 GB（19%），全部在 `devices/1096` 内部 | 逐文件 sha256 |
| 只上传每个问题一份代表证据（跨 host） | 43 个 MTK 问题，25 个目录，3.80 GB（3.4%） | 按厂商去重规则模拟，用 Run 1085 的真实 merge 校验：问题数、`Count`、`DeviceCount` 均一致，字节差 0.002% |
| 只做主机汇总、不跨 host | 84 个问题，46 个目录，7.09 GB（6.4%） | 同上 |

结论：决定字节量的是「上传什么」，不是「上传后是否按内容去重」。ADR-0053 的内容寻址在 Run 1096 内最多省 19%；把顺序改为先去重后上传，可以把同一个 run 降到 3.4%。

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

### D1 先汇总去重，再上传

1. Agent 不得在控制面回传选择结果之前，把采用本 ADR 的 PlanRun 的事件证据上传到中心存储。
2. 中心存储只接收控制面 merge 选定的代表证据。主机报表、控制面 merge 报表和累计统计导出文件不受此限。

### D2 三层职责

1. Agent 必须按 PlanRun 执行本机扫描与主机汇总（MTK 为 `start_log_scan -m 0` 加 `-dedup_org`），并把主机报表上送控制面。
2. 控制面必须按 PlanRun 收齐本轮各 host 的主机报表后执行控制面 merge，维护该 PlanRun 的问题清单与累计统计，并为每个新问题选定代表事件。
3. 控制面必须把选定结果回传给持有该代表事件的 Agent。
4. Agent 必须只上传被选中的代表事件，并按 D5 的目录契约写入中心存储。上送、校验与回执沿用 EventUploader 现有链路；Agent 在中心确认前不得删除本地副本（#1083）。
5. 问题身份、问题与事件的对应、代表事件的选择都以控制面数据库为准。任何组件不得从中心目录名、报表文件名或 xls 文本反推这些身份。

### D3 周期

1. 控制面必须在 PlanRun 运行中按固定间隔触发一轮 D2 流程，默认间隔 6 小时。间隔必须是可配置项。
2. 控制面必须在 PlanRun 进入终态后再触发最后一轮。
3. 每一轮必须是增量的：已选定代表的问题只更新累计统计，不得重新选择或重新上传。

### D4 代表证据

1. 控制面必须选择该问题第一次出现、且 dblog、mobilelog、bugreport 三件齐全的那个事件作代表。
2. 同一问题在其他设备或后续轮次再次出现时，控制面只更新累计统计，不得要求补传证据。
3. 本轮没有三件齐全的事件时，控制面不得为该问题选代表，留到后续轮次再选。PlanRun 最后一轮仍没有齐全事件时，控制面必须选第一次出现的事件，并把该问题标为「证据不全」。（第 3 条是规划者提案，待 Owner 裁决，见 §7。）

### D5 中心存储目录契约

1. 采用本 ADR 的 PlanRun 的代表证据必须写入以下结构：

   ```text
   {主PlanRun编号}_{项目名}_{软件版本缩写}_{测试开始时间戳}_MonkeyAEEinfo/
   └── {SN}/
       └── {事件目录名}/        # 设备上的原名，形如 {时间戳}_db.{XX}
           ├── dblog
           ├── mobilelog
           ├── bugreport
           └── 累计统计导出文件
   ```

2. 主 PlanRun 是链式执行的根 run（`plan_run.root_plan_run_id`），没有链时是 PlanRun 本身。
3. `{项目名}_{软件版本缩写}_{测试开始时间戳}` 必须按离线工具
   [stability_Offline-MonkeyTestTool](https://github.com/DUElost/automation-toolkit/tree/main/python-tools/stability_Offline-MonkeyTestTool)
   的 `_get_aee_log_folder_name` 规则生成。该规则从设备属性
   `ro.product.name`、`ro.build.display.id`、`ro.build.version.incremental`、`ro.build.version.release` 推导，含按产品分支的特例。
   STP 必须把它实现为按项目可扩展、带版本的规则，新增项目时只增加规则，不改已有规则。
4. 一个事件目录可能包含多个问题。同一事件目录被多个问题选为代表时，Agent 只上传一次，累计统计导出文件列出该目录承载的全部问题。
5. 累计统计导出文件是数据库的副本，供提单与人工查看；权威在控制面数据库（D6）。

### D6 问题与累计统计

1. 问题实体沿用 ADR-0056 D2 已接受的 `issue_signature` 与 `issue_occurrence`。本 ADR 新增「PlanRun × 问题」一级的汇总与代表选择记录，表名由实施方案按单数命名。
2. 累计统计至少包括出现次数与机器数，取自控制面 merge 报表的 `Count` 与 `DeviceCount` 列，按 [`2026-scan-upload-merge-contract.md`](../design/2026-scan-upload-merge-contract.md) 的表头契约按列名消费。
3. 本条修订 ADR-0053 D7 规则 3：同一 PlanRun 内的签名级去重与累计计数由 STP 负责。跨 PlanRun 是否并入既有 JIRA 单，仍归提单工具与 JIRA。
4. ADR-0053 D7 规则 1、2、4、5（同一发生跨 run 不重复提单、baseline 默认不进提单输入、身份无法证明时 fail-open、是否已提单只查长期记录）继续有效。

### D7 Agent 本地保留

1. Agent 必须把未被选中的事件保留到该 PlanRun 最后一轮汇总完成后再过 N 天，以便补取。
2. N 由 Owner 在 Agent 本地缓冲测量完成后裁定；裁定前，Agent 不得因本 ADR 新增删除路径。

### D8 平台范围

1. 本 ADR 只覆盖 MTK。展锐沿用现行链路，主文件夹名预留为 `…_MonkeyYloginfo`，由后续批次合入。
2. 旧数据不迁移。采用本 ADR 之前的 run 继续留在 `devices/` 与 `jira/`；确认已提单后按 run 归档或删除，删除前必须单独取得 Owner 确认。

### D9 提单工具与报表格式

1. 提单工具必须改为读取 D5 的目录与累计统计导出文件。提单工具与汇总去重工具均为自研，由提单工具一侧做兼容。
2. 主机报表与控制面 merge 报表的内部格式保持现状。需要新增工具参数时，实施者必须先提出并取得 Owner 同意。

## 4. 对既有 ADR 的处理

| ADR | 处理 | 理由 |
|---|---|---|
| ADR-0053 D1–D6、Phase A–D | Superseded | 中心存储只存每个问题一份代表证据后，内容寻址要消除的跨 run / 跨事件副本基本不再产生；保留它的复杂度（blob、manifest、引用 GC）没有对应收益 |
| ADR-0053 D7 | 规则 3 按本 ADR D6 第 3 条修订，其余保留 | 见 D6 |
| ADR-0025 D4、ADR-0028 | 对采用本 ADR 的 PlanRun，触发时机、上传选择依据与落点按本 ADR 执行；extract 与 `jira/{run}` 副本不再产生 | 见 D1–D5 |
| ADR-0056 D2 | 沿用，作为 D6 的问题实体 | 不另造身份 |

## 5. 放弃的备选

| 备选 | 结论 | 理由 |
|---|---|---|
| 保持先上传后去重，实施 ADR-0053 内容寻址 | 否决 | Run 1096 最多省 19%，本 ADR 的顺序调整省 96.6% |
| 只做主机汇总就上传（不等控制面 merge） | 否决 | 6.4% 对 3.4%，且跨 host 的同一问题仍会重复上传；回传链路是本 ADR 的必要成本 |
| 只在 PlanRun 终态做一轮 | 否决 | 长时间运行的 PlanRun 中 Agent 本地缓冲会持续增长，且问题要等到结束才可见 |
| 中心存储只放 brief、证据全部按需拉取 | 否决 | 提单需要代表证据随时可取；Owner 选定每个问题上传一份完整证据 |

## 6. 迁移与激活（按 ADR-0058 批次 B8）

1. 前置：#3829（字节台账）与 #3831（项目名、版本来源）先按单点修复。
2. 阶段一（影子运行）：控制面按 D3 周期执行 merge 与代表选择，只写数据库，不改变上传。用真实 PlanRun 对拍影子结果与现行链路的问题数、累计统计和应上传字节。
3. 阶段二（切换上传）：按 PlanRun 开关启用 D1、D4、D5。开关关闭时沿用现行链路，作为回退路径。
4. 阶段三（提单切换）：提单工具改读 D5 目录（D9）。验收后，新 run 不再执行 extract。
5. 旧数据处理按 D8 第 2 条单独确认。

## 7. 待 Owner 裁决

1. D4 第 3 条：没有三件齐全事件时的处理（规划者提案：等到最后一轮，仍不齐全则选第一次出现并标「证据不全」）。
2. 同一 PlanRun 内设备软件版本不一致时，主文件夹名取哪一台设备的属性（规划者提案：取该 PlanRun 第一个产生代表证据的设备，并在累计统计导出文件中列出全部版本）。
3. ADR-0053 D1–D6 改为 Superseded，以及 #3308 随之关闭。

## 8. 复议触发

- 影子运行中，控制面 merge 的问题数或累计统计与现行 merge 不一致。
- Agent 本地缓冲测量显示 D7 的保留期无法放下单个 PlanRun 的全部事件。
- 展锐合入时，主机汇总工具的报表形态无法满足 D2 第 1 条。
