## 1. PipelineEngine 基础设施修复

- [x] 1.1 `PipelineEngine.__init__` 新增 `local_db=None` 参数，存为 `self._local_db`
- [x] 1.2 `main.py` 中 `execute_pipeline_task()` 构造 PipelineEngine 时传入已有的 `local_db` 实例
- [x] 1.3 `StepContext` dataclass 新增 `local_db: Any = None` 字段
- [x] 1.4 `_execute_step_stages()` 构造 StepContext 时传入 `local_db=self._local_db`
- [x] 1.5 `_execute_step_stages()` 在 return 前补充 `if result.metrics: self._shared[step_id] = result.metrics`（修复 stages 路径 shared 写入缺失）
- [x] 1.6 `_execute_stages_format()` 在执行前对 pipeline_def 中的 `{log_dir}` 占位符做字符串替换（值来自 `self._log_dir`）

## 2. 新增 Action: setup_device_commands

- [x] 2.1 在 `device_actions.py` 中实现 `setup_device_commands(ctx) -> StepResult`，按序执行 `commands` 列表中的 ADB shell 命令，支持 per-command timeout 和 on_failure(continue/stop)
- [x] 2.2 在 `actions/__init__.py` 的 `ACTION_REGISTRY` 中注册 `setup_device_commands`

## 3. 新增 Action: guard_process

- [x] 3.1 在 `process_actions.py` 中实现 `guard_process(ctx) -> StepResult`，通过 pgrep -f 检测进程存活、多实例去重、死亡时按参数执行重启流程
- [x] 3.2 在 `actions/__init__.py` 的 `ACTION_REGISTRY` 中注册 `guard_process`

## 4. 增强 Action: scan_aee 增量模式

- [x] 4.1 在 `file_actions.py` 的 `scan_aee` 中新增 `incremental`、`whitelist_file`、`state_key_prefix` 参数处理，incremental=false 时行为不变
- [x] 4.2 实现增量逻辑：从 LocalDB 读取已处理条目集、解析 db_history（逗号分隔，取列 0/8/9）、diff 新增条目、仅 pull 新增、写回 LocalDB。字段数 < 10 跳过并 log warning
- [x] 4.3 实现白名单过滤：读取 whitelist_file 并过滤 aee_exp 类型条目（列 8 pkg_name），缓存到 shared["_whitelist"]
- [x] 4.4 将 new_timestamps 写入 metrics 供 shared dict 传递给下游 export_mobilelogs

## 5. 新增 Action: export_mobilelogs

- [x] 5.1 在 `file_actions.py` 中实现 `export_mobilelogs(ctx) -> StepResult`，从 shared 读取 AEE 时间戳、解析 mobilelog 目录名时间戳、匹配时间窗口、adb pull 匹配目录
- [x] 5.2 在 `actions/__init__.py` 的 `ACTION_REGISTRY` 中注册 `export_mobilelogs`

## 6. 增强 Action: aee_extract 批量模式

- [x] 6.1 在 `log_actions.py` 的 `aee_extract` 中新增 `batch`、`max_workers`、`retry_limit`、`min_free_disk_gb`、`state_key_prefix` 参数处理，batch=false 时行为不变
- [x] 6.2 实现批量逻辑：磁盘空间检查、os.walk 递归发现 .dbg 文件、LocalDB 重试状态跟踪、ThreadPoolExecutor 并行解密

## 7. Pipeline 模板

- [x] 7.1 创建 `backend/schemas/pipeline_templates/monkey_aee_init.json` 初始化模板（所有占位符使用 `{...}` 格式，不含硬编码凭据）
- [x] 7.2 创建 `backend/schemas/pipeline_templates/monkey_aee_patrol.json` 巡检模板
- [x] 7.3 在 `aimonkey.json` 的 description 中添加 `[DEPRECATED]` 前缀标记废弃

## 8. 调度器增强

- [x] 8.1 在 CronScheduler `_fire_schedule()` 中增加重叠保护：按 workflow_definition_id 查询 RUNNING 状态（排除超时 stale Run），跳过重叠触发并更新 next_run_at
- [x] 8.2 在 `_tick()` 末尾增加历史 Run 清理逻辑：删除超过 WORKFLOW_RUN_RETENTION_DAYS（默认 3 天）的已终结 WorkflowRun，每次最多 100 条

## 9. 单元测试

- [x] 9.1 编写 `setup_device_commands` 测试：全部成功、on_failure=stop 中断、空列表、命令超时
- [x] 9.2 编写 `guard_process` 测试：进程存活、进程死亡+重启成功、多实例清理、资源缺失、max_restarts 限制
- [x] 9.3 编写 `scan_aee` 增量模式测试：全量模式不变、增量首次（空状态）、增量第二次（只拉新增）、白名单过滤、local_db=None fallback
- [x] 9.4 编写 `export_mobilelogs` 测试：有匹配、无匹配、空 timestamps、时间窗口边界
- [x] 9.5 编写 `aee_extract` 批量模式测试：单文件模式不变、批量扫描、并行解密、retry_limit 跳过、磁盘空间不足
- [x] 9.6 编写 PipelineEngine shared 写入修复的回归测试：stages 路径执行后 shared dict 包含 step metrics
