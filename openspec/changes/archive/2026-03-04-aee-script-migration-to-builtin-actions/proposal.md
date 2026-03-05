# OPSX Proposal: AEE 脚本移植 — builtin action 化 + 短周期巡检调度

**Change ID**: aee-script-migration-to-builtin-actions
**Date**: 2026-03-03
**Status**: Proposal
**Type**: Feature — Architecture + Implementation

---

## Context

### 现状

当前 AEE 稳定性测试通过手动 SSH 到 Linux 主机执行单体 Python 脚本：

```bash
timeout 604800 nohup python3 MonkeyAEEinfo_Stability_20250901.py 0124 > script_0124.log 2>&1 &
```

该脚本（2139 行）是一个 **7 天长驻守护进程**，内部管理多设备并行、3 分钟轮询、JSON 缓存持久化、文件锁节点协调。

### 目标

将脚本能力迁移到平台的 **builtin action + pipeline stages + 短周期调度 Workflow** 体系中：

1. **公共能力 builtin 化** — 设备预检、root、资源推送、进程启动/监控/停止、AEE 拉取、解密、日志扫描
2. **专项差异参数化** — Monkey/DDR/GPU/MTBF/Standby 通过 action template 传不同 `command`/`resources`/`log_paths`/`timeout`/`retry`
3. **调度替代守护** — 短周期巡检 workflow（3-5 分钟）替代单进程 7 天循环
4. **仅保留不可通用化的核心执行器为 `tool:<id>`**

### 核心架构转换

```
原脚本：多设备单进程（1 script → N devices, while-loop 7天）
平台：  单设备单 job（1 workflow dispatch → fan-out N jobs, 调度器循环触发）
```

---

## Requirements

### R1: 新增 / 增强 builtin actions

#### R1.1 ~~新增 `builtin:setup_mobilelog`~~ → 合并入 R1.6

> **Design 决策**: AM broadcast 本质就是 ADB shell 命令，`setup_device_commands`（R1.6）完全覆盖此需求，无需独立 action。最终实际变更为：新增 3 个 action（R1.3/R1.4/R1.6）+ 增强 2 个（R1.2/R1.5）= **共 5 项变更**。

**场景**: 脚本 `_perform_initial_device_setup()` 中向 `com.debug.loggerui` 发送多条 AM broadcast 命令（启动日志、设置大小、设置分包）。当前 `clean_env` 不支持 AM broadcast。

**约束**:
- 命令列表参数化（不硬编码 loggerui 的 broadcast）
- 支持 `settings put`、`setprop`、`am broadcast` 三类命令
- 每条命令独立超时 + 失败可选 continue/stop

#### R1.2 增强 `builtin:scan_aee` → 支持增量模式

**场景**: 当前 `scan_aee` 做全量 pull，脚本的 `process_device_logs()` 读设备端 `db_history` 文件做增量 diff 只拉新条目。7 天运行期间增量模式避免重复拉取 GB 级日志。

**约束**:
- 新增 `incremental: true` 参数
- 增量模式下：读 `db_history` → 解析条目（逗号分隔，取列 0/8/9）→ 对比 Agent LocalDB 中的已处理集合 → 仅 pull 新增
- 支持白名单过滤（`whitelist_file` 参数）
- 增量状态通过 Agent LocalDB（SQLite WAL）跨 Run 持久化（参见 R3.3 / C2）；单次 Run 内的 `new_timestamps` 通过 `shared` dict 传递给下游 step

#### R1.3 新增 `builtin:monitor_process_by_name`（或增强 `monitor_process`）

**场景**: 脚本的 `check_and_manage_monkey_process()` 按进程名（非 PID）检测存活，发现死亡后**自动重启**；发现多实例则杀掉冗余。当前 `monitor_process` 仅支持 PID 监控，无重启能力。

**约束**:
- 支持 `process_name` 参数（`pgrep -f <name>`）
- 支持 `restart_command` 参数（进程死亡时执行）
- 支持 `max_restart` 限制（防无限重启）
- 多实例检测 + 清理冗余

#### R1.4 新增 `builtin:export_mobilelogs`

**场景**: 脚本的 `export_correlated_mobilelogs()` 按 AEE 异常时间戳，从设备 `/data/debuglogger/mobilelog/` 中匹配时间窗口内的日志目录并拉取。当前无对应 action。

**约束**:
- 输入：AEE 时间戳列表（从 `scan_aee` 的 `shared` 输出获取）
- 行为：解析 mobilelog 目录名中的时间戳 → 找最近匹配 → `adb pull`
- 输出到与 AEE 日志同级目录

#### R1.5 增强 `builtin:aee_extract` → 支持批量 + 并行

**场景**: 脚本的 `process_local_undecrypted_logs()` 批量扫描本地 `.dbg` 文件，4 线程并行解密，有重试限制和磁盘空间检查。当前 `aee_extract` 是单文件单次调用。

**约束**:
- 支持 `input_dir` 为目录时递归扫描所有 `.dbg` 文件
- 支持 `max_workers` 参数（默认 4）
- 支持 `retry_limit` 参数（默认 2）
- 执行前检查磁盘空间（`min_free_disk_gb` 参数）

#### R1.6 新增 `builtin:setup_device_commands`

**场景**: 脚本在设备初始化时执行一组有序的 ADB shell 命令（`settings put`、`setprop`、`am broadcast`、`cmd wifi` 等），命令列表因测试类型不同而异。

**约束**:
- 参数 `commands`: `[{"cmd": "shell settings put ...", "timeout": 10}, ...]`
- 按序执行，每条独立超时
- 失败策略：`on_failure` 可选 `continue`（默认） / `stop`
- 与 `clean_env` 的区别：`clean_env` 聚焦卸载+清理，`setup_device_commands` 聚焦正向配置

### R2: Action Template 参数化

#### R2.1 不同测试类型共享同一套 builtin actions，通过参数差异化

| 参数维度 | Monkey (AEE) | DDR | GPU | MTBF | Standby |
|---------|-------------|-----|-----|------|---------|
| `start_process.command` | `nohup /data/local/tmp/aim --pkg-blacklist-file ...` | DDR 专用命令 | GPU benchmark 命令 | MTBF runner | sleep cycle script |
| `push_resources.files` | `aim.jar`, `aim`, `aimwd`, `monkey.apk`, `blacklist.txt` | DDR 工具集 | GPU shader 文件 | MTBF APK | sleep APK |
| `monitor_process.process_name` | `com.android.commands.monkey.transsion` | DDR 进程名 | GPU 进程名 | MTBF 进程名 | — |
| `scan_aee.incremental` | `true` | `true` | `true` | `true` | `true` |
| `fill_storage.target_percentage` | 60 | 0（不填充） | 0 | 60 | 0 |
| Pipeline 超时 | 604800 (7d) | 259200 (3d) | 86400 (1d) | 604800 (7d) | 604800 (7d) |

#### R2.2 模板存储位置

利用现有 `backend/schemas/pipeline_templates/*.json` 或新增 `ActionTemplate` 数据库表（已有 migration `f4a5b6c7d8e9`）。

### R3: 短周期巡检调度

#### R3.1 Workflow 拆分为两个阶段

**初始化 Workflow**（触发一次）:
```
stages:
  prepare: [check_device, ensure_root, setup_device_commands, connect_wifi, fill_storage]
  execute: [push_resources, start_process]
```

**巡检 Workflow**（每 3-5 分钟循环触发）:
```
stages:
  prepare: [check_device, ensure_root]
  execute: [monitor_process_by_name, scan_aee(incremental), export_mobilelogs]
  post_process: [aee_extract(batch), log_scan]
```

#### R3.2 调度器能力需求

当前 `backend/api/routes/schedules.py` 已有定时调度框架。需确认：
- 支持 cron 表达式或 interval 触发
- 支持 "前一轮未完成则跳过本轮" 防重叠
- 每次触发等效于 `POST /api/v1/workflows/{id}/run`，由 dispatcher fan-out 到所有设备

#### R3.3 增量状态跨 Workflow Run 持久化

**关键约束**: 原脚本通过 `processed_log_cache.json` 在循环间保持增量状态。平台每次 Workflow Run 是独立的 Job，`StepContext.shared` 不跨 Run 持久化。

**方案**:
- Agent 侧利用已有的 `LocalDB`（SQLite WAL）存储增量状态
- `scan_aee(incremental)` 从 LocalDB 读取已处理条目集合，执行完写回
- 键：`{device_serial}:{aee_type}:processed_entries`

### R4: 不可通用化的专项执行器保留为 `tool:<id>`

经分析，当前 AEE 脚本中**没有不可通用化的核心执行器**。所有功能都可以分解为上述 builtin actions 的参数化组合。

潜在的 `tool:<id>` 候选（未来其他测试类型可能需要）：
- 自定义的 instrumentation test runner（超出 `run_instrument` 能力范围的）
- 专项硬件控制脚本（温控箱、充电柜）

---

## Success Criteria

| ID | 验收条件 | 验证方式 |
|----|---------|---------|
| SC1 | 所有 R1 中的 builtin actions 通过单元测试 | `pytest backend/tests/` |
| SC2 | Monkey AEE 初始化 Workflow 能在真实设备上完成设备配置 + 启动 Monkey | 手动触发 + 矩阵页确认 |
| SC3 | Monkey AEE 巡检 Workflow 单次执行能完成：进程守护 + 增量 AEE 拉取 + 解密 + 日志扫描 | 手动触发 + StepTrace 确认 |
| SC4 | 调度器能以 3-5 分钟间隔循环触发巡检 Workflow，且不重叠 | 观察 WorkflowRun 历史列表 |
| SC5 | 增量状态跨 Workflow Run 持久化有效（第 N 轮不重复拉取第 N-1 轮已处理的日志） | 对比两轮 StepTrace 的 `metrics.pulled` 计数 |
| SC6 | DDR/GPU/MTBF/Standby 测试类型能通过不同的 pipeline_template 参数复用同一套 actions | 创建不同类型的 WorkflowDefinition 并触发 |

---

## Constraints (from research)

| ID | 类型 | 约束 |
|----|------|------|
| C1 | 架构 | 原脚本是"多设备单进程"，平台是"单设备单 job"。所有设备并行由 dispatcher fan-out 实现，action 内部只处理单设备 |
| C2 | 数据 | 增量状态不能放 `StepContext.shared`（仅单次 Run 内有效），需用 Agent LocalDB 持久化 |
| C3 | 依赖 | `aee_extract` 二进制文件需预部署到 Agent 主机，路径通过 action params 或 Agent config 传入 |
| C4 | 依赖 | 资源文件（`aim.jar`、`monkey.apk` 等）需通过 Tool 分发机制或 NFS 挂载获取，`push_resources` 的 `local` 路径指向 Agent 主机上的文件 |
| C5 | 调度 | 巡检 Workflow 需要"前一轮未完成则跳过"机制，防止累积未完成的 WorkflowRun |
| C6 | 兼容 | 现有 `clean_env` / `scan_aee` / `aee_extract` 的 API 签名不能 breaking change，只做向后兼容的参数扩展 |
| C7 | 时序 | 初始化 Workflow 必须在巡检 Workflow 之前完成；调度器需支持"依赖前置 Workflow 完成"或由用户手动先触发初始化 |

---

## Scope

### In Scope
- 新增 3 个 + 增强 2 个 builtin actions（R1.2 - R1.6；R1.1 合并入 R1.6，见下方说明）
- Monkey AEE 的 pipeline_template（初始化 + 巡检）
- Agent LocalDB 增量状态存储
- 调度器循环触发能力（如果现有 schedules.py 不满足则增强）
- 参数化模板使 DDR/GPU/MTBF/Standby 可复用

### Out of Scope
- 脚本中的 NFS 挂载逻辑（C4 — 由部署脚本处理）
- 脚本中的节点协调/文件锁（C1 — 由 dispatcher fan-out 天然替代）
- 前端 UI 变更（使用现有编排/执行页面）
- `storage_filler.py` 移植（现有 `fill_storage` 用 dd 实现，功能等价）

---

## Risks

| ID | 风险 | 缓解 |
|----|------|------|
| RK1 | 巡检 Workflow 高频触发（每 3 分钟）可能产生大量 WorkflowRun/JobInstance 记录 | 需要数据清理策略或归档机制 |
| RK2 | 增量 AEE 扫描依赖 `db_history` 文件格式稳定 | 格式解析加防御性错误处理 |
| RK3 | 批量解密 4 线程并行可能在低配 Agent 主机上造成 CPU 压力 | `max_workers` 参数化，默认保守值 |
| RK4 | 调度器"跳过未完成轮次"逻辑如果实现不当，可能永久跳过 | 加入超时强制终止 + 告警 |
