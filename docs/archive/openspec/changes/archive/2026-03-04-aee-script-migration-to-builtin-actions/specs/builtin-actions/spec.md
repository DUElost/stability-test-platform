# Spec: Builtin Actions — 新增与增强

**Module**: `backend/agent/actions/`
**Change**: aee-script-migration-to-builtin-actions

---

## Overview

扩展 Agent 的 builtin action 库，使其能完整承载 AEE 稳定性测试脚本的所有功能。新增 3 个 action，增强 2 个现有 action，扩展 StepContext 以支持跨 Run 持久化状态。

---

## StepContext 扩展

### 变更: 新增 `local_db` 字段

**文件**: `backend/agent/pipeline_engine.py`

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
    local_db: Any = None  # 新增: LocalDB instance
```

**现状**: `PipelineEngine.__init__` 当前 **没有** `local_db` 参数（`pipeline_engine.py:51`），`main.py:484` 构造 `PipelineEngine` 时也未传入。需要：

1. `PipelineEngine.__init__` 新增 `local_db=None` 关键字参数，存为 `self._local_db`
2. `_execute_step_stages()` 构造 `StepContext` 时传入 `local_db=self._local_db`
3. `main.py` 中构造 `PipelineEngine` 时传入已初始化的 `local_db` 实例

**影响范围**: 所有 action 可选使用 `ctx.local_db`。不使用时为 `None`，现有 action 无需修改。

---

## 新增 Action: `setup_device_commands`

### 位置

`backend/agent/actions/device_actions.py`

### 签名

```python
def setup_device_commands(ctx: StepContext) -> StepResult
```

### 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `commands` | `list[dict]` | 是 | — | 有序命令列表 |

每个 command dict:

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `cmd` | `str` | 必填 | ADB shell 命令（不含 `adb -s xxx shell` 前缀） |
| `timeout` | `int` | `15` | 单条命令超时（秒） |
| `on_failure` | `str` | `"continue"` | `"continue"` 或 `"stop"` |

### 行为

1. 校验 `commands` 非空，否则返回 `StepResult(success=True, metrics={"executed": 0})`
2. 按序遍历，调用 `ctx.adb.shell(ctx.serial, cmd, timeout=timeout)`
3. 捕获 `AdbError` 和 `Exception`：
   - `on_failure == "stop"` → 立即返回失败
   - `on_failure == "continue"` → 记录错误，继续下一条
4. 全部命令完成后（或遇 stop 失败后），返回结果

### 返回

```python
StepResult(
    success=(failed == 0 or all_failures_are_continue),
    exit_code=0 if success else 1,
    error_message="; ".join(errors) if errors else "",
    metrics={"executed": int, "failed": int, "errors": list[str]}
)
```

当所有失败命令的 `on_failure` 均为 `"continue"` 时，`success=True`。任何 `"stop"` 失败导致 `success=False`。

### 注册

`ACTION_REGISTRY["setup_device_commands"] = setup_device_commands`

---

## 新增 Action: `guard_process`

### 位置

`backend/agent/actions/process_actions.py`

### 签名

```python
def guard_process(ctx: StepContext) -> StepResult
```

### 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `process_name` | `str` | 是 | — | `pgrep -f` 匹配模式 |
| `restart_command` | `str` | 否 | `""` | 进程死亡时执行的 shell 命令 |
| `pre_restart_commands` | `list[str]` | 否 | `[]` | 重启前依次执行的 shell 命令 |
| `max_restarts` | `int` | 否 | `3` | 单次 action 执行中的最大重启次数 |
| `resource_check_path` | `str` | 否 | `""` | 重启前检查此路径是否存在 |

### 行为

```
1. pgrep -f {process_name} → 解析 PID 列表

2. if len(pids) > 1:
     保留 pids[0]，kill -9 pids[1:]
     return success, status="deduplicated"

3. if len(pids) == 1:
     return success, status="alive", pid=pids[0]

4. if len(pids) == 0:
     if resource_check_path:
       check = adb shell "[ -f {path} ] && echo exists"
       if check != "exists":
         return failure, status="resource_missing"

     if not restart_command:
       return failure, status="dead_no_restart_cmd"

     for cmd in pre_restart_commands:
       adb shell cmd (timeout=15, 忽略失败)

     adb shell restart_command (timeout=30)
     sleep(3)

     re-check pgrep → if alive:
       return success, status="restarted"
     else:
       return failure, status="restart_failed"
```

### 返回

```python
StepResult(
    success=bool,
    metrics={
        "status": "alive" | "deduplicated" | "restarted" | "resource_missing" | "restart_failed" | "dead_no_restart_cmd",
        "pid": str or "",
        "restart_count": int,
        "killed_duplicates": int
    }
)
```

### 注册

`ACTION_REGISTRY["guard_process"] = guard_process`

---

## 增强 Action: `scan_aee` — 增量模式

### 位置

`backend/agent/actions/file_actions.py`（原有函数扩展）

### 新增参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `incremental` | `bool` | `false` | 启用增量模式 |
| `whitelist_file` | `str` | `""` | AEE 白名单文件路径（host 侧，每行一个包名） |
| `state_key_prefix` | `str` | `"scan_aee"` | LocalDB 状态键前缀 |

现有参数 `aee_dirs` 和 `local_dir` **不变**。

### 行为（`incremental=false` 时）

完全不变 — 现有全量 pull 逻辑。

### 行为（`incremental=true` 时）

```
for each aee_dir in aee_dirs:
  aee_type = "vendor_aee_exp" if "vendor" in aee_dir else "aee_exp"
  state_key = f"{prefix}:{serial}:{aee_type}:processed_entries"

  1. processed_set = json.loads(ctx.local_db.get_state(state_key, "[]"))
     processed_set = set(processed_set)

  2. history_output = adb shell "cat {aee_dir}/db_history"
     if empty/error → skip this aee_dir

  3. Parse each line → split by comma, extract (db_path=col[0], pkg_name=col[8], timestamp=col[9])
     Fields < 10 → skip line with warning (defensive parsing)

  4. if whitelist_file and aee_type == "aee_exp":
       load whitelist (cached in shared["_whitelist"])
       filter: keep only lines where pkg_name in whitelist

  5. current_set = set(all_parsed_lines)
     new_entries = current_set - processed_set

  6. For each new entry:
       local_target = os.path.join(local_dir, aee_type, formatted_dirname)
       adb pull db_path → local_target
       Collect timestamp for downstream

  7. ctx.local_db.set_state(state_key, json.dumps(list(current_set)))

  8. Populate metrics with new_timestamps for shared dict
```

### 返回

```python
StepResult(
    success=True,
    metrics={
        "scanned": int,          # db_history 总条目数
        "pulled": int,           # 实际拉取的新增条目数
        "skipped_known": int,    # 已处理跳过的条目数
        "filtered_whitelist": int,  # 白名单过滤掉的条目数
        "new_timestamps": list[str],  # 新增 AEE 的时间戳列表 (供 export_mobilelogs 使用)
        "errors": int            # pull 失败次数
    }
)
```

### 向后兼容

- `incremental` 默认 `false` → 旧调用不受影响
- 增量模式需要 `ctx.local_db is not None`，否则 fallback 到全量模式并 log warning

---

## 新增 Action: `export_mobilelogs`

### 位置

`backend/agent/actions/file_actions.py`

### 签名

```python
def export_mobilelogs(ctx: StepContext) -> StepResult
```

### 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `timestamps_from_step` | `str` | 是 | — | shared 中的 step name，取其 `new_timestamps` |
| `mobilelog_path` | `str` | 否 | `"/data/debuglogger/mobilelog/"` | 设备端 mobilelog 根目录 |
| `local_dir` | `str` | 是 | — | 本地输出目录 |
| `time_window_minutes` | `int` | 否 | `30` | 时间窗口匹配精度（分钟） |

### 行为

```
1. timestamps = ctx.shared[timestamps_from_step]["new_timestamps"]
   if not timestamps → return success(pulled=0)

2. ls_output = adb shell "ls {mobilelog_path}"
   Parse directory names → extract datetime from pattern "APLog_YYYY_MMDD_HHMMSS"

3. For each aee_timestamp:
     Find mobilelog dir with closest datetime within time_window_minutes
     if found:
       adb pull "{mobilelog_path}/{dir_name}" → "{local_dir}/{dir_name}"

4. Return metrics
```

### 时间戳解析

支持两种 mobilelog 目录名格式：
- `APLog_YYYY_MMDD_HHMMSS` → `datetime(YYYY, MM, DD, HH, MM, SS)`
- `APLog_YYYY_MM_DD_HH_MM_SS`（备用格式）

AEE 时间戳格式由 `scan_aee` 输出决定（ISO 格式 `YYYY-MM-DDTHH:MM:SS` 或 `db_history` 中的原始格式）。

### 返回

```python
StepResult(
    success=True,  # 即使部分 unmatched 也算成功
    metrics={
        "matched": int,
        "pulled": int,
        "unmatched_timestamps": list[str]
    }
)
```

### 注册

`ACTION_REGISTRY["export_mobilelogs"] = export_mobilelogs`

---

## 增强 Action: `aee_extract` — 批量模式

### 位置

`backend/agent/actions/log_actions.py`（原有函数扩展）

### 新增参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `batch` | `bool` | `false` | 启用批量目录模式 |
| `max_workers` | `int` | `4` | 并行解密线程数 |
| `retry_limit` | `int` | `2` | 单文件最大重试次数 |
| `min_free_disk_gb` | `int` | `10` | 最低可用磁盘空间（GB） |
| `state_key_prefix` | `str` | `"aee_decrypt"` | LocalDB 状态键前缀 |

现有参数 `input_dir`、`output_dir`、`tool_path` **不变**。

### 行为（`batch=false` 时）

完全不变。

### 行为（`batch=true` 时）

```
1. disk_check: shutil.disk_usage(input_dir)
   if free_gb < min_free_disk_gb → return success(skipped="low_disk")

2. dbg_files = []
   for root, dirs, files in os.walk(input_dir):
     for f in files:
       if f.endswith(".dbg"):
         dbg_files.append(os.path.join(root, f))

3. Load failure_state from LocalDB:
   failures = json.loads(ctx.local_db.get_state(f"{prefix}:failures", "{}"))
   Skip files where failures[path] >= retry_limit

4. ThreadPoolExecutor(max_workers=max_workers):
   For each dbg_file:
     output_path = dbg_file.replace(".dbg", "_decoded")
     result = subprocess.run([tool_path, dbg_file, output_path], timeout=300)
     if failed:
       failures[dbg_file] = failures.get(dbg_file, 0) + 1

5. Save failures back to LocalDB

6. Return metrics
```

### 返回

```python
StepResult(
    success=True,  # 部分失败不阻断 pipeline
    metrics={
        "total_found": int,
        "decrypted": int,
        "failed": int,
        "skipped_retry_limit": int,
        "skipped_low_disk": bool
    }
)
```

### 向后兼容

- `batch` 默认 `false` → 旧调用不受影响
- 批量模式下若 `ctx.local_db is None`，则不跟踪重试状态（每次全量重试）

---

## ACTION_REGISTRY 变更

```python
# backend/agent/actions/__init__.py

from .device_actions import (
    check_device, clean_env, push_resources,
    ensure_root, fill_storage, connect_wifi, install_apk,
    setup_device_commands,       # 新增
)
from .process_actions import (
    start_process, monitor_process, stop_process, run_instrument,
    guard_process,               # 新增
)
from .file_actions import (
    adb_pull, collect_bugreport, scan_aee,
    export_mobilelogs,           # 新增
)
from .log_actions import aee_extract, log_scan
from .tool_actions import run_tool_script

ACTION_REGISTRY = {
    # ... 现有 17 个不变 ...
    "setup_device_commands": setup_device_commands,
    "guard_process": guard_process,
    "export_mobilelogs": export_mobilelogs,
}
```

---

## db_history 解析格式

原脚本 `_parse_db_history_line()` 解析格式参考（`MonkeyAEEinfo_Stability_20250901.py:1233`）：

每行 `db_history` 格式为 **逗号分隔** 的字段序列（原脚本使用 `line.split(",")` 并取固定列）：
```
field[0],field[1],field[2],...,field[8],field[9],...
```

解析提取（以原脚本列号为准）：
- `db_path`: 列 0（设备端绝对路径，用于 `adb pull`）
- `pkg_name`: 列 8（进程/包名，用于白名单过滤）
- `timestamp`: 列 9（时间戳，用于 mobilelog 关联）

实现时应采用防御性解析：字段数不足 10 时跳过该行并 log warning。

---

## 测试要求

每个新增/增强 action 需要对应的单元测试：

| Action | 测试文件 | 关键 Case |
|--------|---------|----------|
| `setup_device_commands` | `test_device_actions.py` | 全部成功、on_failure=stop 中断、空列表、超时 |
| `guard_process` | `test_process_actions.py` | 进程存活、进程死亡+重启成功、多实例清理、资源缺失、max_restarts 限制 |
| `scan_aee` (增量) | `test_file_actions.py` | 全量模式不变、增量首次（空状态）、增量第二次（只拉新增）、白名单过滤、local_db=None fallback |
| `export_mobilelogs` | `test_file_actions.py` | 有匹配、无匹配、空 timestamps、时间窗口边界 |
| `aee_extract` (批量) | `test_log_actions.py` | 单文件模式不变、批量扫描、并行解密、retry_limit 跳过、磁盘空间不足 |
