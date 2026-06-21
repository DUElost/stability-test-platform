# Design: AEE 脚本移植 — builtin action 化 + 短周期巡检调度

**Change ID**: aee-script-migration-to-builtin-actions
**Date**: 2026-03-03

---

## 1. Architecture Overview

### 1.1 模型转换

```
原脚本                              平台等价
─────────────────────────────────────────────────────────────────
main_loop (7 天 while True)    →  CronScheduler 每 3 分钟触发 WorkflowRun
多设备 ThreadPoolExecutor       →  dispatcher.dispatch_workflow fan-out N个 JobInstance
MonkeyTest.start_test()        →  初始化 Workflow (一次性)
_run_device_tasks() 每轮        →  巡检 Workflow (循环触发)
processed_log_cache.json       →  Agent LocalDB agent_state 表
NodeCoordinator 文件锁          →  JobInstance 原子 claim (平台天然解决)
```

### 1.2 两条 Pipeline 定义

**初始化 Pipeline** (`monkey_aee_init`):
```
stages:
  prepare:
    1. check_device
    2. ensure_root
    3. setup_device_commands  ← 新增 action
    4. connect_wifi
    5. fill_storage
  execute:
    6. push_resources (AIMonkey 套件 + blacklist + 脚本)
    7. start_process (Monkey 进程, background=true)
  post_process: []
```

**巡检 Pipeline** (`monkey_aee_patrol`):
```
stages:
  prepare:
    1. check_device
    2. ensure_root
  execute:
    3. guard_process           ← 新增 action (按名检测+守护)
    4. scan_aee (incremental)  ← 增强现有 action
    5. export_mobilelogs       ← 新增 action
  post_process:
    6. aee_extract (batch)     ← 增强现有 action
    7. log_scan
```

---

## 2. Action API Design

所有 action 遵循现有签名 `(ctx: StepContext) -> StepResult`。

### 2.1 新增 `builtin:setup_device_commands`

**文件**: `backend/agent/actions/device_actions.py`

**替代 proposal 中的 R1.1 和 R1.6**：统一为一个通用命令执行器，既覆盖 mobilelog broadcast 也覆盖任意配置命令。

```python
def setup_device_commands(ctx: StepContext) -> StepResult:
    """Execute an ordered list of ADB commands for device initialization."""
```

**参数 (`ctx.params`)**:
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `commands` | `list[dict]` | `[]` | 每项：`{"cmd": str, "timeout": int(=15), "on_failure": "continue"\|"stop"}` |

**行为**:
1. 遍历 `commands` 列表，按序执行 `ctx.adb.shell(ctx.serial, cmd, timeout=timeout)`
2. 每条命令独立超时，失败根据 `on_failure` 决定是否终止
3. 返回 `metrics`: `{"executed": int, "failed": int, "errors": [str]}`

**Monkey AEE 模板中的 params 示例**:
```json
{
  "commands": [
    {"cmd": "settings put global development_settings_enabled 1", "timeout": 10},
    {"cmd": "setprop persist.vendor.mtk.aee.mode 3", "timeout": 10},
    {"cmd": "am broadcast -a com.debug.loggerui.ADB_CMD -e cmd_name start --ei cmd_target 1 -n com.debug.loggerui/.framework.LogReceiver", "timeout": 15},
    {"cmd": "am broadcast -a com.debug.loggerui.ADB_CMD -e cmd_name set_total_log_size_4096 --ei cmd_target 1 -n com.debug.loggerui/.framework.LogReceiver", "timeout": 15},
    {"cmd": "settings put global development_settings_enabled 0", "timeout": 10}
  ]
}
```

**决策**: 将 proposal 的 R1.1 `setup_mobilelog` 和 R1.6 `setup_device_commands` **合并为一个 action**。AM broadcast 本质就是 ADB shell 命令，无需独立 action。通过参数列表即可表达 mobilelog 启动、AEE 模式设置等任何组合。

### 2.2 新增 `builtin:guard_process`

**文件**: `backend/agent/actions/process_actions.py`

**替代 proposal R1.3**。命名 `guard_process` 而非 `monitor_process_by_name`（更简短且语义准确 — "守护"而非"监控"）。

```python
def guard_process(ctx: StepContext) -> StepResult:
    """Check process by name, restart if dead, deduplicate if multiple."""
```

**参数 (`ctx.params`)**:
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `process_name` | `str` | 必填 | `pgrep -f` 匹配模式 |
| `restart_command` | `str` | `""` | 进程死亡时执行的 shell 命令 |
| `pre_restart_commands` | `list[str]` | `[]` | 重启前执行的命令列表（如 `dumpsys activity appops on`） |
| `max_restarts` | `int` | `3` | 单次执行最大重启次数 |
| `resource_check_path` | `str` | `""` | 重启前检查关键文件是否存在 |
| `full_setup_step` | `str` | `""` | 资源不存在时 fallback 的 shared step name（读取其 metrics 获取 setup info） |

**行为**:
1. `pgrep -f {process_name}` 获取 PID 列表
2. 多实例：保留第一个，`kill -9` 其余
3. 零实例：
   a. 若 `resource_check_path` 设定 → 检查文件存在性
   b. 资源存在 → 执行 `pre_restart_commands` → 执行 `restart_command`
   c. 资源不存在 → 返回 `StepResult(success=False, error_message="resource missing, need full setup")`
4. 一实例：正常，返回成功

**返回 `metrics`**:
```python
{"status": "alive"|"restarted"|"resource_missing", "pid": str, "restart_count": int}
```

**与现有 `monitor_process` 的关系**: 不修改 `monitor_process`。两者职责不同 — `monitor_process` 是长时间持续监控（循环检查 PID 直到退出），`guard_process` 是单次快照式检查 + 恢复。在巡检 pipeline 的语义下，每 3 分钟检查一次，不需要持续循环。

### 2.3 增强 `builtin:scan_aee` — 增量模式

**文件**: `backend/agent/actions/file_actions.py`

**向后兼容扩展**：新增参数，不改变现有参数的默认行为。

**新增参数**:
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `incremental` | `bool` | `false` | 启用增量模式 |
| `whitelist_file` | `str` | `""` | AEE 白名单文件路径（host 侧） |
| `state_key_prefix` | `str` | `"scan_aee"` | LocalDB 状态键前缀 |

**行为（`incremental=true` 时）**:
1. 从 `ctx.local_db.get_state(f"{prefix}:{ctx.serial}:{aee_type}")` 读取已处理条目集（JSON 反序列化为 set）
2. 对每个 `aee_dir`，执行 `adb shell cat {aee_dir}/db_history` 获取当前条目列表
3. 解析 `db_history` 每行：格式为 **逗号分隔**（`line.split(",")` 取列 0/8/9）
   - 列 0: `db_path`（设备端绝对路径，用于 adb pull）
   - 列 8: `pkg_name`（用于白名单过滤）
   - 列 9: `timestamp`（时间戳，用于 mobilelog 关联）
4. 如果提供 `whitelist_file` 且 `aee_type == "aee_exp"` → 过滤非白名单条目
5. Diff：`new_entries = current_set - processed_set`
6. 仅 pull 新增条目对应的 `db_path`
7. 写回 `ctx.local_db.set_state(key, json.dumps(list(current_set)))`
8. `metrics`: `{"scanned": int, "pulled": int, "skipped_known": int, "new_timestamps": [str]}`
   - `new_timestamps` 存入 `shared` 供下游 `export_mobilelogs` 使用

**StepContext 扩展**：需要在 `StepContext` 中暴露 `local_db` 引用。

### 2.4 新增 `builtin:export_mobilelogs`

**文件**: `backend/agent/actions/file_actions.py`

```python
def export_mobilelogs(ctx: StepContext) -> StepResult:
    """Pull mobilelog directories correlated with AEE timestamps."""
```

**参数**:
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `timestamps_from_step` | `str` | `""` | 从 shared 中读取的 step name（取 `new_timestamps` 字段） |
| `mobilelog_path` | `str` | `"/data/debuglogger/mobilelog/"` | 设备端 mobilelog 目录 |
| `local_dir` | `str` | 必填 | 本地输出目录 |
| `time_window_minutes` | `int` | `30` | 时间窗口匹配精度 |

**行为**:
1. 从 `ctx.shared[timestamps_from_step]["new_timestamps"]` 获取 AEE 时间戳列表
2. `adb shell ls {mobilelog_path}` 获取所有 mobilelog 目录名
3. 解析目录名中的时间戳（格式 `APLog_YYYY_MMDD_HHMMSS`）
4. 对每个 AEE 时间戳，找时间窗口内最近的 mobilelog 目录
5. `adb pull` 匹配的目录到 `local_dir`

**返回 `metrics`**: `{"matched": int, "pulled": int, "unmatched_timestamps": [str]}`

### 2.5 增强 `builtin:aee_extract` — 批量模式

**文件**: `backend/agent/actions/log_actions.py`

**向后兼容扩展**。

**新增参数**:
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `batch` | `bool` | `false` | 启用批量模式 |
| `max_workers` | `int` | `4` | 并行解密线程数 |
| `retry_limit` | `int` | `2` | 单文件最大重试次数 |
| `min_free_disk_gb` | `int` | `10` | 最低磁盘空间（GB）|
| `state_key_prefix` | `str` | `"aee_decrypt"` | LocalDB 状态键前缀（跟踪失败计数） |

**行为（`batch=true` 时）**:
1. 检查磁盘空间 `shutil.disk_usage(input_dir)`
2. 递归 `os.walk(input_dir)` 找 `*.dbg` 文件
3. 从 LocalDB 读取 `{prefix}:failures` 获取已达重试上限的文件列表 → 跳过
4. `ThreadPoolExecutor(max_workers)` 并行调用 `aee_extract` 二进制
5. 对失败文件累计失败次数，达到 `retry_limit` 后标记跳过
6. `metrics`: `{"total_found": int, "decrypted": int, "failed": int, "skipped_retry_limit": int}`

**与单文件模式的兼容**: 当 `batch=false`（默认）时，行为完全不变。

### 2.6 不新增的 action

| Proposal 项 | 决策 | 理由 |
|-------------|------|------|
| R1.1 `setup_mobilelog` | **合并到 R1.6** | AM broadcast 就是 ADB shell 命令，`setup_device_commands` 完全覆盖 |
| R1.6 `setup_device_commands` | **实现** | 见 2.1 |

最终新增 3 个 action + 增强 2 个现有 action = **共 5 项变更**。

---

## 3. StepContext 扩展

### 3.1 新增 `local_db` 字段

```python
@dataclass
class StepContext:
    adb: Any
    serial: str
    params: dict
    run_id: int
    step_id: int
    logger: Any
    shared: dict = field(default_factory=dict)
    local_db: Any = None  # ← 新增: LocalDB instance, 可选
```

**传入时机**: `PipelineEngine.__init__` 当前 **没有** `local_db` 参数（`pipeline_engine.py:51`），`main.py:484` 构造时也未传入。需要：

1. `PipelineEngine.__init__` 新增 `local_db=None` 参数，存为 `self._local_db`
2. `_execute_step_stages()` 构造 `StepContext` 时传入 `local_db=self._local_db`
3. `main.py` 中 `execute_pipeline_task()` 构造 `PipelineEngine` 时传入已有的 `local_db` 实例

**为什么不用 shared dict**: `shared` 仅在单次 pipeline 执行期间有效。增量状态需要跨 WorkflowRun 持久化 7 天，必须用 LocalDB。

### 3.2 LocalDB agent_state 键命名规范

```
scan_aee:{serial}:aee_exp:processed_entries    → JSON array of db_history lines
scan_aee:{serial}:vendor_aee_exp:processed_entries
aee_decrypt:failures                           → JSON dict {filepath: attempt_count}
```

---

## 4. Pipeline Template 设计

### 4.1 Monkey AEE 初始化 (`monkey_aee_init.json`)

```json
{
  "version": 1,
  "description": "Monkey AEE stability test - device initialization (run once)",
  "stages": {
    "prepare": [
      {"step_id": "check_device", "action": "builtin:check_device", "timeout_seconds": 30, "retry": 1, "params": {}},
      {"step_id": "ensure_root", "action": "builtin:ensure_root", "timeout_seconds": 30, "retry": 2, "params": {"max_attempts": 3}},
      {"step_id": "setup_commands", "action": "builtin:setup_device_commands", "timeout_seconds": 120, "retry": 0, "params": {
        "commands": [
          {"cmd": "settings put global development_settings_enabled 1", "timeout": 10},
          {"cmd": "setprop persist.vendor.mtk.aee.mode 3", "timeout": 10},
          {"cmd": "am broadcast -a com.debug.loggerui.ADB_CMD -e cmd_name start --ei cmd_target 1 -n com.debug.loggerui/.framework.LogReceiver", "timeout": 15},
          {"cmd": "am broadcast -a com.debug.loggerui.ADB_CMD -e cmd_name set_total_log_size_4096 --ei cmd_target 1 -n com.debug.loggerui/.framework.LogReceiver", "timeout": 15},
          {"cmd": "am broadcast -a com.debug.loggerui.ADB_CMD -e cmd_name set_sublog_4_5_0 --ei cmd_target 1 -n com.debug.loggerui/.framework.LogReceiver", "timeout": 15},
          {"cmd": "settings put global development_settings_enabled 0", "timeout": 10}
        ]
      }},
      {"step_id": "connect_wifi", "action": "builtin:connect_wifi", "timeout_seconds": 30, "retry": 1, "params": {
        "ssid": "{wifi_ssid}", "password": "{wifi_password}"
      }},
      {"step_id": "fill_storage", "action": "builtin:fill_storage", "timeout_seconds": 300, "retry": 0, "params": {
        "target_percentage": 60
      }}
    ],
    "execute": [
      {"step_id": "push_monkey_resources", "action": "builtin:push_resources", "timeout_seconds": 600, "retry": 1, "params": {
        "files": [
          {"local": "/opt/stability-test-agent/resources/aim.jar", "remote": "/data/local/tmp/aim.jar"},
          {"local": "/opt/stability-test-agent/resources/aim", "remote": "/data/local/tmp/aim", "chmod": "777"},
          {"local": "/opt/stability-test-agent/resources/aimwd", "remote": "/data/local/tmp/aimwd", "chmod": "777"},
          {"local": "/opt/stability-test-agent/resources/monkey.apk", "remote": "/data/local/tmp/monkey.apk"},
          {"local": "/opt/stability-test-agent/resources/blacklist.txt", "remote": "/sdcard/blacklist.txt"},
          {"local": "/opt/stability-test-agent/resources/MonkeyTestAi.sh", "remote": "/data/local/tmp/MonkeyTest.sh", "chmod": "777"}
        ]
      }},
      {"step_id": "start_monkey", "action": "builtin:start_process", "timeout_seconds": 30, "retry": 0, "params": {
        "command": "/data/local/tmp/aim --pkg-blacklist-file /sdcard/blacklist.txt --smartuiautomator true --hprof --ignore-crashes --ignore-security-exceptions --ignore-timeouts --throttle 500 --runtime-minutes 10080 --switchuimode -v",
        "background": true
      }}
    ],
    "post_process": []
  }
}
```

### 4.2 Monkey AEE 巡检 (`monkey_aee_patrol.json`)

```json
{
  "version": 1,
  "description": "Monkey AEE stability test - patrol cycle (triggered every 3-5 min)",
  "stages": {
    "prepare": [
      {"step_id": "check_device", "action": "builtin:check_device", "timeout_seconds": 30, "retry": 1, "params": {}},
      {"step_id": "ensure_root", "action": "builtin:ensure_root", "timeout_seconds": 30, "retry": 2, "params": {"max_attempts": 3}}
    ],
    "execute": [
      {"step_id": "guard_monkey", "action": "builtin:guard_process", "timeout_seconds": 60, "retry": 0, "params": {
        "process_name": "com.android.commands.monkey.transsion",
        "restart_command": "nohup /data/local/tmp/aim --pkg-blacklist-file /sdcard/blacklist.txt --smartuiautomator true --hprof --ignore-crashes --ignore-security-exceptions --ignore-timeouts --throttle 500 --runtime-minutes 10080 --switchuimode -v >/dev/null 2>&1 &",
        "pre_restart_commands": ["dumpsys activity appops on"],
        "resource_check_path": "/data/local/tmp/MonkeyTest.sh",
        "max_restarts": 1
      }},
      {"step_id": "scan_aee", "action": "builtin:scan_aee", "timeout_seconds": 300, "retry": 0, "params": {
        "aee_dirs": ["/data/aee_exp", "/data/vendor/aee_exp"],
        "local_dir": "{log_dir}/aee",
        "incremental": true,
        "whitelist_file": "/opt/stability-test-agent/resources/AEE_whitelist.txt"
      }},
      {"step_id": "export_mobilelogs", "action": "builtin:export_mobilelogs", "timeout_seconds": 300, "retry": 0, "params": {
        "timestamps_from_step": "scan_aee",
        "local_dir": "{log_dir}/mobilelog"
      }}
    ],
    "post_process": [
      {"step_id": "decrypt_aee", "action": "builtin:aee_extract", "timeout_seconds": 600, "retry": 0, "params": {
        "input_dir": "{log_dir}/aee",
        "tool_path": "/opt/stability-test-agent/tools/aee_extract",
        "batch": true,
        "max_workers": 4,
        "min_free_disk_gb": 10
      }},
      {"step_id": "log_scan", "action": "builtin:log_scan", "timeout_seconds": 120, "retry": 0, "params": {
        "input_dir": "{log_dir}",
        "keywords": ["FATAL", "CRASH", "ANR"]
      }}
    ]
  }
}
```

### 4.3 专项差异化示例

替换巡检模板中的关键参数即可适配其他测试类型：

| 测试类型 | `guard_process.process_name` | `guard_process.restart_command` | `fill_storage.target_percentage` |
|---------|-----|-----|-----|
| DDR | `ddr_test_process` | DDR 专用启动命令 | 0（跳过） |
| GPU | `gpu_benchmark` | GPU 专用启动命令 | 0 |
| MTBF | `mtbf_runner` | MTBF 专用启动命令 | 60 |
| Standby | — (无 guard) | — | 0 |

Standby 类型的巡检 pipeline 中不包含 `guard_process` 步骤。

---

## 5. 调度器增强

### 5.1 现状

`CronScheduler._tick()` 每 30 秒轮询一次 `task_schedules`，匹配 `next_run_at <= now` 的记录，调用 `dispatch_workflow`。

**缺失能力**: 无重叠保护 — 如果前一轮 WorkflowRun 尚未完成，下一轮仍会触发新 Run。

### 5.2 重叠保护设计

在 `_fire_schedule()` 中增加检查：

```python
def _fire_schedule(self, db, sched, now):
    if sched.workflow_definition_id:
        # --- 新增: 重叠保护 ---
        patrol_timeout_minutes = int(os.getenv("PATROL_TIMEOUT_MINUTES", "10"))
        stale_cutoff = now - timedelta(minutes=patrol_timeout_minutes)
        active_count = db.query(WorkflowRun).filter(
            WorkflowRun.workflow_definition_id == sched.workflow_definition_id,
            WorkflowRun.status == "RUNNING",
            WorkflowRun.started_at > stale_cutoff,  # 超时的 RUNNING 不计入
        ).count()
        if active_count > 0:
            logger.info("cron_skip_overlap schedule_id=%s active_runs=%d", sched.id, active_count)
            # 仍更新 next_run_at 以免下次 tick 再次匹配同一时间
            sched.next_run_at = _compute_next_run(sched.cron_expression, now)
            return
        # --- 正常 dispatch ---
        ...
```

**注意**: 重叠检查基于 `workflow_definition_id` 维度（同一 Workflow 定义不并发），而非 `device_ids` 维度。原因：`dispatch_workflow` 已对设备做 fan-out，同一 definition 的重叠意味着上一轮还未完成，此时应跳过整批。

### 5.3 Stale RUNNING 超时处理

超过 `PATROL_TIMEOUT_MINUTES`（默认 10 分钟）的 RUNNING WorkflowRun 不计入活跃数，避免一个 stuck Run 永久阻塞调度。此超时 **不会** 主动终止 Run，仅让调度器忽略它。Stuck Run 的清理由 heartbeat_monitor 的超时检测负责。

### 5.4 历史 Run 清理

3 分钟间隔 × 7 天 = ~3360 个 WorkflowRun × N 个 JobInstance。

3 分钟间隔：`*/3 * * * *`
5 分钟间隔：`*/5 * * * *`

`CRON_POLL_INTERVAL` 需从默认 30s 确保 ≤ 调度间隔的一半。当前 30s 默认值满足 3 分钟间隔的需求（60s 内检测到到期 schedule）。

### 5.4 历史 Run 清理

3 分钟间隔 × 7 天 = ~3360 个 WorkflowRun × N 个 JobInstance。

**方案**: 在 `CronScheduler._tick()` 末尾追加清理逻辑（每次 tick 仅清理少量记录，避免阻塞）：

```python
# 清理超过 retention_days 的已终结 WorkflowRun
RETENTION_DAYS = int(os.getenv("WORKFLOW_RUN_RETENTION_DAYS", "3"))
cutoff = now - timedelta(days=RETENTION_DAYS)
stale_runs = db.query(WorkflowRun).filter(
    WorkflowRun.status.in_(["SUCCESS", "FAILED", "PARTIAL_SUCCESS", "DEGRADED"]),
    WorkflowRun.created_at < cutoff,
).limit(100).all()
# CASCADE delete JobInstance/StepTrace
for run in stale_runs:
    db.delete(run)
```

**触发频率**: 由 `_tick()` 30 秒轮询驱动，每次最多清理 100 条，低负载无积压。

---

## 6. 资源文件分发

### 6.1 Agent 主机目录结构

```
/opt/stability-test-agent/
├── resources/                    ← Monkey/DDR/GPU 等测试资源
│   ├── aim.jar
│   ├── aim
│   ├── aimwd
│   ├── monkey.apk
│   ├── blacklist.txt
│   ├── MonkeyTestAi.sh
│   ├── AEE_whitelist.txt
│   └── arm64-v8a/
│       └── ...
├── tools/
│   └── aee_extract              ← AEE 解密二进制
├── logs/
└── db/
    └── agent.db                 ← LocalDB (SQLite WAL)
```

### 6.2 资源部署方式

资源文件通过以下方式分发到 Agent 主机：

1. **NFS 挂载**（当前方案）：Agent 主机已挂载 `172.21.15.4` 中央存储，`push_resources` 的 `local` 路径直接指向挂载目录
2. **Tool 分发机制**（未来）：将资源打包为 Tool，由 `ToolRegistry.resolve()` 自动同步版本
3. **安装脚本**：`install_agent.sh` 在部署时将资源复制到 `/opt/stability-test-agent/resources/`

pipeline_template 中的 `local` 路径使用绝对路径 `/opt/stability-test-agent/resources/xxx`，由部署流程保证文件存在。

---

## 7. 跨步骤数据流

### 7.1 单次 Pipeline 内 — shared dict

> **已知缺陷**: 当前 stages 路径（`_execute_step_stages()` at `pipeline_engine.py:505`）执行完 action 后 **不会** 将 `result.metrics` 写入 `self._shared[step_id]`。仅 legacy 路径（`_execute_step()` at `pipeline_engine.py:300`）有此逻辑。
>
> **修复要求**: 在 `_execute_step_stages()` 的 `return result` 前，添加：
> ```python
> if result.metrics:
>     self._shared[step_id] = result.metrics
> ```
> 此修复是 `export_mobilelogs` 读取 `ctx.shared["scan_aee"]["new_timestamps"]` 的前提。

```
start_process (step_name: "start_monkey")
  → metrics: {"pid": "1234", "command": "..."}
  → shared["start_monkey"] = {"pid": "1234", "command": "..."}

guard_process
  → reads: ctx.shared (not needed for name-based check)

scan_aee (incremental)
  → metrics: {"new_timestamps": ["2026-03-03T10:15:22", ...], "pulled": 5}
  → shared["scan_aee"] = {"new_timestamps": [...], "pulled": 5}

export_mobilelogs
  → reads: ctx.shared["scan_aee"]["new_timestamps"]
```

### 7.2 跨 Pipeline Run — LocalDB

```
Run #1 (10:00):
  scan_aee → pulls 20 entries → writes set(20) to LocalDB

Run #2 (10:03):
  scan_aee → reads set(20) from LocalDB
           → device has 22 entries total
           → diff: 2 new → pulls 2 → writes set(22) to LocalDB

Run #N (Day 7):
  scan_aee → reads set(5000) from LocalDB → diff → pulls only new
```

---

## 8. ACTION_REGISTRY 变更汇总

```python
ACTION_REGISTRY = {
    # 现有（不变）
    "check_device": check_device,
    "clean_env": clean_env,
    "push_resources": push_resources,
    "ensure_root": ensure_root,
    "fill_storage": fill_storage,
    "connect_wifi": connect_wifi,
    "install_apk": install_apk,
    "start_process": start_process,
    "monitor_process": monitor_process,
    "stop_process": stop_process,
    "run_instrument": run_instrument,
    "adb_pull": adb_pull,
    "collect_bugreport": collect_bugreport,
    "scan_aee": scan_aee,           # ← 增强: +incremental 模式
    "aee_extract": aee_extract,     # ← 增强: +batch 模式
    "log_scan": log_scan,
    "run_tool_script": run_tool_script,
    # 新增
    "setup_device_commands": setup_device_commands,   # ← 新 action
    "guard_process": guard_process,                   # ← 新 action
    "export_mobilelogs": export_mobilelogs,           # ← 新 action
}
```

总计：17 个现有 → 20 个（新增 3 个，增强 2 个，0 个 breaking change）。

---

## 9. 实施顺序

```
Phase 1: Action 实现（无外部依赖）
  ├─ StepContext 增加 local_db 字段
  ├─ setup_device_commands (新)
  ├─ guard_process (新)
  ├─ scan_aee 增量模式 (增强)
  ├─ export_mobilelogs (新)
  └─ aee_extract 批量模式 (增强)

Phase 2: Pipeline 模板 + 调度器
  ├─ monkey_aee_init.json 模板
  ├─ monkey_aee_patrol.json 模板
  ├─ CronScheduler 重叠保护
  └─ WorkflowRun 清理策略

Phase 3: 集成验证
  ├─ 单元测试 (全部 action)
  ├─ 模板验证 (pipeline_validator)
  └─ 端到端手动测试 (真实设备)
```
