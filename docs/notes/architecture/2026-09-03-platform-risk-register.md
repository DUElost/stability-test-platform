# 平台下一阶段全景隐患与风险台账 (Risk Register)

Status: proposed
Class: architecture

## Decision

在平台下一阶段架构演进准备期，基于机房实网生产控制面、48 台在线宿主机、497 台物理真机（MTK 319 台 / 展锐 279 台）与生产数据库现场审计，建立平台级**潜在隐患与风险登记簿（Risk Register）**。

本记录严格遵守「仅记录客观技术事实与潜在失效场景，不做主观决策」原则，作为后续 P0/P1/P2 各里程碑推进排期的代码级单一事实源（SSOT）。同时在 GitHub 侧建立 Master 汇总 Issue（[#777](https://github.com/DUElost/stability-test-platform/issues/777)）并挂载进 [Project #3 演进规划看板](https://github.com/users/DUElost/projects/3)。

### 隐患与风险全景总表

| 隐患编号 | 所属维度 | 隐患/问题名称 | 风险等级 | 核心事实与源码位置 | 潜在故障影响与失效场景 |
|:---:|:---:|---|:---:|---|---|
| **R-01** | 存储治理 | 原始日志缺乏 TTL 自动淘汰机制 | **Critical (严重)** | `/mnt/stp-aee/devices` 堆积 **485GB** 历史日志（占盘 63%），代码检索确认除 token 外全平台无任何文件清理/TTL 任务。 | 916GB 磁盘在未来数周长跑中必然被打满（100% Full），引发操作系统 I/O 挂死、全平台日志无法落盘。 |
| **R-02** | 控制面 | 同步数据库连接池配置缺失（仅 15 连接） | **High (高危)** | `backend/core/database.py:57-61` 中异步池设了 30+60，但同步池 `get_sync_engine_kwargs` 未设参数，直接回退默认 5+10。 | 多任务并发触发时，后台调度线程（Recycler、PrecheckReaper、AdmissionPump、SAQ）共享 15 连接，极易引发 `QueuePool` 耗尽报错。 |
| **R-03** | 芯片驱动 | 占 46% 的展锐设备缺乏自动化刷机支持 | **High (高危)** | 全网 497 台真机中展锐占 **279 台**；核心脚本 `flash_firmware.py` 仅支持 MTK SP Flash Tool，PAC 与高通刷机为 0 覆盖。 | 展锐机型无法通过平台自动化升级底层固件，大版本切换需人工逐台线刷，阻断全自动回归链路。 |
| **R-04** | 硬件安全 | 缺乏设备电池高温告警与保护熔断 | **High (高危)** | `backend/agent/device_discovery.py:367` 定期解析 `battery_temp`，但后端与 Agent 均未设置任何超温防御/停测机制。 | 真机 72 小时高温高压长跑下若散热不良（温度 >45℃），平台仍会持续施压，存在电池起鼓或硬件损坏隐患。 |
| **R-05** | 资产运维 | 真机缺乏机架物理拓扑与 USB 切电自愈 | **Medium (中危)** | `Device` 表缺少 `rack_id`/`slot_id` 物理字段；全工程无可编程 USB Hub（`uhubctl`）断电复位接口。 | 手机物理损坏时测试人员无法在 500 台机架中快速定位具体机位；底层假死手机无法通过硬件切电冷启动自愈。 |
| **R-06** | 控制面 | SAQ Worker 进程耦合与多实例竞争隐患 | **Medium (中危)** | `backend/tasks/saq_worker.py` 受 `STP_ENABLE_INPROCESS_SAQ=1` 约束内嵌在 Web 进程；`socketio_redis.py` 默认关闭。 | 多副本容器部署时每个副本均启动一个 Worker 争抢同一队列任务，且 WebSocket 信令无法跨实例透传广播。 |
| **R-07** | 套件体系 | 项目级参数分层未闭环 & MTBF 无失败短路 | **Medium (中危)** | `backend/models/project.py:8` `variables` 在 ORM 标注为 D4 挂起；`backend/services/mtbf_suite.py` 缺少连续失败快速熔断（Fail-Fast）。 | 跨项目复用 Plan 存在环境变量残留串染风险；关键前置用例失效时后续 100+ 用例仍机械空跑浪费设备算力。 |
| **R-08** | 数据持久 | 高频日志表持续膨胀缺乏分区管理 | **Low (技术债)** | 现场实测 `audit_logs` 达 **25 万行 (80MB)**，`step_trace` 达 **4.8 万行 (25MB)**，均为单表单向递增，未设分区策略。 | 历史数据无限累积，长期长跑下将导致只读查询延迟变长，索引体积与缓存压力增加。 |
| **R-09** | AI 赋能 | AI 助手工具面偏离核心测试归因业务 | **Low (效能债)** | `backend/services/ai_assistant/tools.py` 已注册 26 个运维只读工具，无任何 JIRA 历史缺陷检索、查重或 Crash 堆栈特征聚类工具。 | 生产已启用的 `deepseek-chat` 只能充当平台操作问答工具，未能切入耗费人力的 Crash 提单与排重核心业务。 |

### 已实测确证的底层瓶颈项映射

- `#730` (P0): Agent 心跳串行同步探测设备导致假死阻塞主循环
- `#731` (P0): 运行日志逐行 open/close 导致系统调用膨胀（微基准高出 71.8 倍）
- `#732` (P0): SAQ scan_task 300s 截止导致 74% 生产运行慢节点报告被抛弃
- `#740` (P0): Agent 抓取 AEE 缺乏主机级槽位限流导致机械盘 I/O 严重寻道翻倍
- `#741` (P1): HddSpillMonitor 腾退限额滞后导致高频崩溃下磁盘不可逆打满

## Alternatives

- **仅在 GitHub Issue 零散开单**：信息碎片化，脱离 git 版本受控体系，离线或不同分支开发时不可见。
- **直接在此阶段提交大规模架构修改**：违反用户当前「仅记录问题与潜在隐患，不做决策」的指令，且风险跨度过宽。

## Verification

- **实网拓扑与 DB 数据**：48 台 ONLINE Host，497 台 ADB Connected 真机（MTK 319 / UNISOC 279）。
- **磁盘物理扫描**：`/mnt/stp-aee` 旋转机械盘已占 578GB（`devices` 485GB, `jira` 76GB）。
- **微基准与故障注入验证**：单机 ADB 超时注入（4.02s 阻塞）、逐行 open/close 评测（40000 次冗余 syscall）、小文件机械寻道（8.92s -> 16.54s）。
- **Master Issue 关联**：GitHub Issue #777 已创建并成功绑定进 Project #3 看板。

## Revisit

当开始执行各对应 Milestone（P0/P1/P2）的特性开发或重构前，重新核对该项事实并在修复后标记 Closed。
