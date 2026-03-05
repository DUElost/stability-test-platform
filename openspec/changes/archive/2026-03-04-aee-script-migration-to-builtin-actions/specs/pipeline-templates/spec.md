# Spec: Pipeline Templates — 初始化 + 巡检拆分

**Module**: `backend/schemas/pipeline_templates/`
**Change**: aee-script-migration-to-builtin-actions

---

## Overview

将现有的单体长时间 `aimonkey.json` 模板（7 天 `monitor_process`）拆分为两个独立模板：

1. **`monkey_aee_init.json`** — 设备初始化（一次性触发）
2. **`monkey_aee_patrol.json`** — 巡检循环（3-5 分钟定时触发）

同时更新其他测试类型模板以统一使用新的 builtin actions。

---

## 现有模板分析

### `aimonkey.json`（将被替代）

当前方案：单个 pipeline 运行 7 天，通过 `monitor_process` 的 `duration: 604800` 实现长驻。

**问题**：
- 一个 step 占用 7 天，无步骤级可观测性
- 无增量 AEE 扫描（仅 post_process 做一次性全量 pull）
- 无 Monkey 进程守护（`monitor_process` 按 PID 监控，进程死亡后 pipeline 结束）
- 不支持 mobilelog 关联导出

### 替代策略

`aimonkey.json` **保留但标记废弃**（description 加 `[DEPRECATED]` 前缀），供过渡期兼容。新 Workflow 使用 `monkey_aee_init.json` + `monkey_aee_patrol.json`。

---

## 新增模板: `monkey_aee_init.json`

**用途**: Monkey AEE 稳定性测试的设备初始化。由用户手动或调度器触发一次。

**文件路径**: `backend/schemas/pipeline_templates/monkey_aee_init.json`

```json
{
  "version": 1,
  "description": "Monkey AEE stability test - device initialization (run once per test cycle)",
  "stages": {
    "prepare": [
      {
        "step_id": "check_device",
        "action": "builtin:check_device",
        "timeout_seconds": 30,
        "retry": 1,
        "params": {}
      },
      {
        "step_id": "ensure_root",
        "action": "builtin:ensure_root",
        "timeout_seconds": 30,
        "retry": 2,
        "params": { "max_attempts": 3 }
      },
      {
        "step_id": "setup_commands",
        "action": "builtin:setup_device_commands",
        "timeout_seconds": 120,
        "retry": 0,
        "params": {
          "commands": [
            { "cmd": "settings put global development_settings_enabled 1", "timeout": 10 },
            { "cmd": "setprop persist.vendor.mtk.aee.mode 3", "timeout": 10 },
            { "cmd": "am broadcast -a com.debug.loggerui.ADB_CMD -e cmd_name start --ei cmd_target 1 -n com.debug.loggerui/.framework.LogReceiver", "timeout": 15 },
            { "cmd": "am broadcast -a com.debug.loggerui.ADB_CMD -e cmd_name set_total_log_size_4096 --ei cmd_target 1 -n com.debug.loggerui/.framework.LogReceiver", "timeout": 15 },
            { "cmd": "am broadcast -a com.debug.loggerui.ADB_CMD -e cmd_name set_sublog_4_5_0 --ei cmd_target 1 -n com.debug.loggerui/.framework.LogReceiver", "timeout": 15 },
            { "cmd": "settings put global development_settings_enabled 0", "timeout": 10 },
            { "cmd": "cmd wifi set-wifi-enabled enabled", "timeout": 10 },
            { "cmd": "cmd wifi start-scan", "timeout": 10 }
          ]
        }
      },
      {
        "step_id": "connect_wifi",
        "action": "builtin:connect_wifi",
        "timeout_seconds": 30,
        "retry": 1,
        "params": { "ssid": "{wifi_ssid}", "password": "{wifi_password}" }
      },
      {
        "step_id": "fill_storage",
        "action": "builtin:fill_storage",
        "timeout_seconds": 300,
        "retry": 0,
        "params": { "target_percentage": 60 }
      }
    ],
    "execute": [
      {
        "step_id": "push_monkey_resources",
        "action": "builtin:push_resources",
        "timeout_seconds": 600,
        "retry": 1,
        "params": {
          "files": [
            { "local": "{resource_dir}/aim.jar", "remote": "/data/local/tmp/aim.jar" },
            { "local": "{resource_dir}/aim", "remote": "/data/local/tmp/aim", "chmod": "777" },
            { "local": "{resource_dir}/aimwd", "remote": "/data/local/tmp/aimwd", "chmod": "777" },
            { "local": "{resource_dir}/aimonkey.apk", "remote": "/data/local/tmp/monkey.apk" },
            { "local": "{resource_dir}/blacklist.txt", "remote": "/sdcard/blacklist.txt" },
            { "local": "{resource_dir}/MonkeyTestAi.sh", "remote": "/data/local/tmp/MonkeyTest.sh", "chmod": "777" }
          ]
        }
      },
      {
        "step_id": "start_aimwd",
        "action": "builtin:start_process",
        "timeout_seconds": 30,
        "retry": 0,
        "params": { "command": "/data/local/tmp/aimwd", "background": true }
      },
      {
        "step_id": "start_monkey",
        "action": "builtin:start_process",
        "timeout_seconds": 30,
        "retry": 0,
        "params": {
          "command": "/data/local/tmp/aim --pkg-blacklist-file /sdcard/blacklist.txt --smartuiautomator true --hprof --ignore-crashes --ignore-security-exceptions --ignore-timeouts --throttle 500 --runtime-minutes 10080 --switchuimode -v",
          "background": true
        }
      }
    ],
    "post_process": []
  }
}
```

### 设计要点

- `setup_commands` 使用新的 `builtin:setup_device_commands`，将原脚本 `_perform_initial_device_setup()` 的所有 ADB 命令参数化
- WiFi 凭证使用 `{wifi_ssid}` / `{wifi_password}` 占位符，创建 WorkflowDefinition 时由用户填入
- `push_monkey_resources` 使用 `{resource_dir}` 占位符指向 Agent 主机上的资源目录
- `start_aimwd` 和 `start_monkey` 分开，避免单步骤过于复杂
- `post_process` 为空 — 初始化阶段不做日志采集

---

## 新增模板: `monkey_aee_patrol.json`

**用途**: Monkey AEE 巡检循环。由 CronScheduler 每 3-5 分钟触发。

**文件路径**: `backend/schemas/pipeline_templates/monkey_aee_patrol.json`

```json
{
  "version": 1,
  "description": "Monkey AEE stability test - patrol cycle (scheduled every 3-5 min)",
  "stages": {
    "prepare": [
      {
        "step_id": "check_device",
        "action": "builtin:check_device",
        "timeout_seconds": 30,
        "retry": 1,
        "params": {}
      },
      {
        "step_id": "ensure_root",
        "action": "builtin:ensure_root",
        "timeout_seconds": 30,
        "retry": 2,
        "params": { "max_attempts": 3 }
      }
    ],
    "execute": [
      {
        "step_id": "guard_monkey",
        "action": "builtin:guard_process",
        "timeout_seconds": 60,
        "retry": 0,
        "params": {
          "process_name": "com.android.commands.monkey.transsion",
          "restart_command": "nohup /data/local/tmp/aim --pkg-blacklist-file /sdcard/blacklist.txt --smartuiautomator true --hprof --ignore-crashes --ignore-security-exceptions --ignore-timeouts --throttle 500 --runtime-minutes 10080 --switchuimode -v >/dev/null 2>&1 &",
          "pre_restart_commands": ["dumpsys activity appops on"],
          "resource_check_path": "/data/local/tmp/MonkeyTest.sh",
          "max_restarts": 1
        }
      },
      {
        "step_id": "scan_aee",
        "action": "builtin:scan_aee",
        "timeout_seconds": 300,
        "retry": 0,
        "params": {
          "aee_dirs": ["/data/aee_exp", "/data/vendor/aee_exp"],
          "local_dir": "{log_dir}/aee",
          "incremental": true,
          "whitelist_file": "{resource_dir}/AEE_whitelist.txt"
        }
      },
      {
        "step_id": "export_mobilelogs",
        "action": "builtin:export_mobilelogs",
        "timeout_seconds": 300,
        "retry": 0,
        "params": {
          "timestamps_from_step": "scan_aee",
          "local_dir": "{log_dir}/mobilelog"
        }
      }
    ],
    "post_process": [
      {
        "step_id": "decrypt_aee",
        "action": "builtin:aee_extract",
        "timeout_seconds": 600,
        "retry": 0,
        "params": {
          "input_dir": "{log_dir}/aee",
          "tool_path": "{tools_dir}/aee_extract",
          "batch": true,
          "max_workers": 4,
          "min_free_disk_gb": 10
        }
      },
      {
        "step_id": "log_scan",
        "action": "builtin:log_scan",
        "timeout_seconds": 120,
        "retry": 0,
        "params": {
          "input_dir": "{log_dir}",
          "keywords": ["FATAL", "CRASH", "ANR"]
        }
      }
    ]
  }
}
```

### 设计要点

- `guard_monkey` 使用新的 `builtin:guard_process`，替代原脚本 `check_and_manage_monkey_process()`
- `scan_aee` 使用增强后的 `incremental: true` 模式 + 白名单
- `export_mobilelogs` 通过 `timestamps_from_step: "scan_aee"` 从 shared dict 读取新 AEE 时间戳
- `decrypt_aee` 使用增强后的 `batch: true` 模式并行解密
- 所有步骤设计为快速完成（单次巡检目标 < 2 分钟），适配 3 分钟调度间隔
- `max_restarts: 1` — 巡检单次最多重启一次 Monkey，避免频繁重启

---

## 现有模板更新: `aimonkey.json`

**变更**: 仅修改 `description` 字段标记废弃，不改变功能。

```diff
- "description": "AIMonkey stability test: root, configure, push resources, fill storage, run aim, monitor, collect logs",
+ "description": "[DEPRECATED: use monkey_aee_init + monkey_aee_patrol] AIMonkey stability test: root, configure, push resources, fill storage, run aim, monitor, collect logs",
```

---

## Pipeline Schema 兼容性

新模板中使用的所有新 action（`setup_device_commands`、`guard_process`、`export_mobilelogs`）均以 `builtin:` 为前缀，符合 `pipeline_schema.json` 的 action pattern `^(tool:\d+|builtin:.+)$`。

**无需修改 JSON Schema**。

新增参数（`incremental`、`batch`、`whitelist_file` 等）位于 `params` 对象内，schema 对 params 不做结构约束（`"type": "object", "default": {}`）。

---

## 模板占位符规范

| 占位符 | 说明 | 典型值 |
|--------|------|--------|
| `{resource_dir}` | Agent 主机上的测试资源目录 | `/opt/stability-test-agent/resources` |
| `{tools_dir}` | Agent 主机上的工具目录 | `/opt/stability-test-agent/tools` |
| `{log_dir}` | Job 运行日志目录 | 由 PipelineEngine 注入 `config.get_run_log_dir(run_id)` |
| `{wifi_ssid}` | WiFi SSID | 由 WorkflowDefinition 创建时填入 |
| `{wifi_password}` | WiFi 密码 | 由 WorkflowDefinition 创建时填入 |

**占位符解析时机与责任划分**:

| 占位符类别 | 解析时机 | 责任方 | 说明 |
|------------|---------|--------|------|
| `{wifi_ssid}`, `{wifi_password}`, `{resource_dir}`, `{tools_dir}` | 创建 WorkflowDefinition 时 | 用户/前端 | 模板中的占位符在用户创建 WorkflowDefinition 时由前端 UI 替换为实际值，存入数据库 |
| `{log_dir}` | Job 执行时 | PipelineEngine | 运行时由 Agent 注入，值为 `config.get_run_log_dir(run_id)`，因每次 Run 的 log_dir 不同 |

**当前状态**: 模板 API（`pipeline.py`）仅做 JSON 加载，不解析占位符。`PipelineEngine` 当前也不做占位符替换。**需要新增**: PipelineEngine 执行前对 `pipeline_def` 中的 `{log_dir}` 做字符串替换。其余占位符由前端在保存 WorkflowDefinition 时替换完毕，PipelineEngine 拿到的已是最终值。

---

## 模板注册

新模板文件放入 `backend/schemas/pipeline_templates/` 目录后，`GET /api/v1/pipeline/templates` 自动发现并返回（通过 `TEMPLATES_DIR.glob("*.json")`），无需额外注册代码。

### 最终模板清单

| 文件名 | 类型 | 状态 |
|--------|------|------|
| `monkey.json` | 基础 Monkey | 不变 |
| `monkey_aee.json` | 旧脚本包装 | 不变 |
| `aimonkey.json` | 旧长驻 AIMonkey | 标记 DEPRECATED |
| **`monkey_aee_init.json`** | **新: 初始化** | **新增** |
| **`monkey_aee_patrol.json`** | **新: 巡检** | **新增** |
| `mtbf.json` | MTBF | 不变 |
| `ddr.json` | DDR | 不变 |
| `gpu.json` | GPU | 不变 |
| `standby.json` | Standby | 不变 |

---

## WorkflowDefinition 使用方式

### 创建 Monkey AEE 测试的完整流程

1. **创建初始化 Workflow**:
   ```
   POST /api/v1/workflows
   {
     "name": "Monkey AEE Init - 202603",
     "task_templates": [{
       "name": "device_init",
       "pipeline_def": <monkey_aee_init.json 内容，占位符已替换>,
       "sort_order": 0
     }]
   }
   ```

2. **创建巡检 Workflow**:
   ```
   POST /api/v1/workflows
   {
     "name": "Monkey AEE Patrol - 202603",
     "task_templates": [{
       "name": "patrol_cycle",
       "pipeline_def": <monkey_aee_patrol.json 内容，占位符已替换>,
       "sort_order": 0
     }]
   }
   ```

3. **手动触发初始化**: `POST /api/v1/workflows/{init_wf_id}/run` + device_ids

4. **创建定时巡检 Schedule**:
   ```
   POST /api/v1/schedules
   {
     "name": "Monkey AEE Patrol Every 3min",
     "cron_expr": "*/3 * * * *",
     "task_type": "WORKFLOW",
     "workflow_definition_id": <patrol_wf_id>,
     "device_ids": [1, 2, 3, ...]
   }
   ```

---

## 专项测试类型复用

其他测试类型创建类似的 init + patrol 模板对：

| 测试类型 | init 差异 | patrol 差异 |
|---------|----------|------------|
| **DDR** | `setup_commands`: DDR 特定属性; `push_resources`: memtester; `fill_storage`: 无 | `guard_process.process_name`: DDR 进程; 无 `export_mobilelogs` |
| **GPU** | `setup_commands`: 无; `install_apk`: benchmark APK; `fill_storage`: 无 | `guard_process.process_name`: GPU 进程; 无 `export_mobilelogs` |
| **MTBF** | 与 Monkey 类似但 `push_resources` 不同 | 与 Monkey 类似但进程名不同 |
| **Standby** | `start_process`: sleep cycle; `fill_storage`: 无 | 无 `guard_process`; 只做 `scan_aee` + `log_scan` |

这些模板在 Phase 2+ 按需创建，本次 change 只实现 Monkey AEE 的模板对。
