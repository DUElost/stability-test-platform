# Agent 开发规范：Pipeline Engine、Tool_Registry、本地可靠性

> 读本文档前请先读 [`../architecture/ARCHITECTURE.md`](../architecture/ARCHITECTURE.md) 和 [`../backend/MQ.md`](../backend/MQ.md)

## 1. Agent 项目结构

```
agent/
├── main.py                   # 入口：启动 Poller、Heartbeat、Control 监听
├── config.py                 # SERVER_URL, HOST_ID, CPU_QUOTA 等
├── pipeline/
│   ├── engine.py             # PipelineEngine：按 Stage 顺序执行 Steps
│   ├── watchdog.py           # 超时监控，触发 adb reboot
│   └── executor.py           # 单 Step 执行：调用 Tool 脚本
├── registry/
│   ├── tool_registry.py      # Tool_ID → 本地路径 映射，版本检查
│   └── local_db.py           # SQLite (WAL 模式) 操作封装
├── mq/
│   ├── producer.py           # 写入 stp:status 和 stp:logs
│   └── control_listener.py   # 监听 stp:control，处理背压和工具更新
├── heartbeat.py              # 定时心跳，携带 tool_catalog_version
└── reconciler.py             # 重连时 Replay 本地缓存
```

## 2. PipelineEngine

```python
class PipelineEngine:
    """
    按照 pipeline_def 的 stages 顺序执行：prepare → execute → post_process
    任意 stage 内的 step 失败（超过 retry 次数）→ 停止后续 stage
    """

    async def run(self, job: JobInstance) -> None:
        pipeline_def = job.pipeline_def
        stages = ["prepare", "execute", "post_process"]

        for stage in stages:
            steps = pipeline_def["stages"].get(stage, [])
            for step in steps:
                success = await self._run_step_with_retry(job, stage, step)
                if not success:
                    await self._report_job_status(job.id, "FAILED",
                                                   reason=f"step_failed:{step['step_id']}")
                    return

        await self._report_job_status(job.id, "COMPLETED")

    async def _run_step_with_retry(self, job, stage, step) -> bool:
        max_retry = step.get("retry", 0)
        for attempt in range(max_retry + 1):
            result = await self._execute_step(job, stage, step)
            if result.success:
                return True
            if attempt < max_retry:
                await asyncio.sleep(5 * (attempt + 1))  # 指数退避
        return False
```

## 3. Tool_Registry

```python
class ToolRegistry:
    """
    维护 tool_id → { script_path, script_class, version } 的本地映射。
    Server 是 source of truth，Agent 本地缓存用于快速解析。
    """

    def __init__(self, db: LocalDB, server_client: ServerClient):
        self._cache: dict[int, ToolEntry] = {}
        self._version: str = ""

    async def initialize(self):
        """Agent 启动时全量拉取"""
        tools = await self.server_client.get_all_tools()
        for t in tools:
            self._cache[t.id] = ToolEntry(
                script_path=t.script_path,
                script_class=t.script_class,
                version=t.version,
            )
        self._version = self._compute_hash()
        self._db.save_tool_cache(self._cache)

    def resolve(self, tool_id: int, required_version: str) -> ToolEntry:
        """
        解析 tool_id 为本地路径。
        版本不一致时抛出 ToolVersionMismatch，由 executor 触发拉取流程。
        """
        entry = self._cache.get(tool_id)
        if not entry:
            raise ToolNotFoundLocally(tool_id)
        if entry.version != required_version:
            raise ToolVersionMismatch(tool_id, entry.version, required_version)
        return entry

    async def pull_tool(self, tool_id: int, version: str) -> bool:
        """
        从 Server 拉取指定版本工具包。
        返回 True 表示成功，失败时由调用方决定 Job 状态（PENDING_TOOL 或 FAILED）。
        """
        for attempt in range(3):
            try:
                tool = await self.server_client.download_tool(tool_id, version)
                self._cache[tool_id] = tool
                self._db.update_tool_cache(tool_id, tool)
                return True
            except NetworkError:
                await asyncio.sleep(2 ** attempt)
        return False
```

## 4. 工具版本拉取失败处理

```python
async def _execute_step(self, job, stage, step) -> StepResult:
    action = step["action"]

    if action.startswith("tool:"):
        tool_id = int(action.split(":")[1])
        version = step["version"]

        try:
            tool_entry = self.registry.resolve(tool_id, version)
        except ToolVersionMismatch:
            # 尝试拉取新版本
            success = await self.registry.pull_tool(tool_id, version)
            if not success:
                # 网络错误：Job → PENDING_TOOL
                await self._report_job_status(job.id, "PENDING_TOOL",
                    reason=f"tool_pull_failed:network tool_id={tool_id} version={version}")
                return StepResult(success=False, abort_pipeline=True)
            try:
                tool_entry = self.registry.resolve(tool_id, version)
            except ToolNotFoundLocally:
                # 版本不存在于 Server：Job → FAILED
                await self._report_job_status(job.id, "FAILED",
                    reason=f"tool_version_not_exist:tool_id={tool_id} version={version}")
                return StepResult(success=False, abort_pipeline=True)
```

## 5. SQLite 本地缓存（local_db.py）

```python
class LocalDB:
    """
    所有写操作必须在事务内完成，使用 WAL 模式。
    """

    def initialize(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")  # 等同于 fsync
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS step_trace_cache (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          INTEGER NOT NULL,
                step_id         TEXT    NOT NULL,
                stage           TEXT    NOT NULL,
                event_type      TEXT    NOT NULL,
                status          TEXT    NOT NULL,
                output          TEXT,
                error_message   TEXT,
                original_ts     TEXT    NOT NULL,
                acked           INTEGER NOT NULL DEFAULT 0,  -- 0: 未 ACK，1: 已 ACK
                UNIQUE(job_id, step_id, event_type)
            )
        """)

    def save_step_trace(self, trace: StepTrace) -> int:
        """
        写入 Step Trace，必须在 trace 落盘后再更新 last_ack_id。
        顺序：INSERT step_trace_cache → COMMIT → （等待 Server ACK） → UPDATE acked=1
        禁止在同一事务中同时更新 acked 字段。
        """
        with self.conn:
            cursor = self.conn.execute("""
                INSERT OR IGNORE INTO step_trace_cache
                (job_id, step_id, stage, event_type, status, output, error_message, original_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (trace.job_id, trace.step_id, trace.stage, trace.event_type,
                  trace.status, trace.output, trace.error_message,
                  trace.original_ts.isoformat()))
            return cursor.lastrowid

    def get_unacked_traces(self, after_id: int) -> list[dict]:
        """Replay 时调用，获取未确认的 StepTrace"""
        return self.conn.execute(
            "SELECT * FROM step_trace_cache WHERE id > ? AND acked=0 ORDER BY original_ts ASC",
            (after_id,)
        ).fetchall()
```

## 6. Watchdog

```python
class StepWatchdog:
    """
    每个 Step 启动时创建，超时后强制终止。
    """

    async def watch(self, job_id: int, step: dict, process: subprocess.Process):
        timeout = step.get("timeout_seconds", 3600)
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # 1. 强杀脚本进程
            process.kill()
            await process.wait()
            # 2. adb reboot 目标设备
            device_serial = await self._get_device_serial(job_id)
            subprocess.run(["adb", "-s", device_serial, "reboot"])
            # 3. 上报 ABORTED
            await self.mq_producer.send_status(job_id, "ABORTED",
                reason=f"watchdog_timeout:step={step['step_id']} timeout={timeout}s")
```

## 7. 资源配额控制

```python
# 在 Agent 启动时从 config 读取
CPU_QUOTA = int(os.environ.get("AGENT_CPU_QUOTA", 2))  # 最大并行分析进程数

# 使用 asyncio.Semaphore 控制 CPU 密集型任务
analysis_semaphore = asyncio.Semaphore(CPU_QUOTA)

async def run_analysis_step(step):
    async with analysis_semaphore:
        # AEE 解密、日志分析等 CPU 密集型操作
        await execute_tool(step)
```

**哪些 Step 需要走 Semaphore**：在 `tool` 表中新增 `resource_type` 字段，值为 `"cpu_intensive"` 的 Tool 执行时必须获取 Semaphore。轻量扫描类 Tool 无需限制。

## 8. 心跳

```python
async def heartbeat_loop():
    while True:
        response = await server_client.heartbeat({
            "host_id":               HOST_ID,
            "tool_catalog_version":  registry.version,
            "load": {
                "running_jobs":  engine.running_job_count(),
                "cpu_percent":   psutil.cpu_percent(),
            }
        })

        # 处理工具目录更新通知
        if response["tool_catalog_outdated"]:
            await registry.initialize()  # 全量重新拉取

        # 处理背压指令
        backpressure = response.get("backpressure", {})
        mq_producer.set_log_rate_limit(backpressure.get("log_rate_limit"))

        await asyncio.sleep(10)
```

---

## 架构约束：禁止使用 BaseTestCase

> **参见 [ADR-0016](../../adr/ADR-0016-deprecate-base-test-case.md)**

`backend/agent/test_framework.py` 中的 `BaseTestCase` **已正式废弃**，严格禁止在 Agent 任何新增代码中使用。

### 强制规则

| 场景 | 禁止 | 要求 |
|------|------|------|
| 新增测试逻辑 | `class MyTest(BaseTestCase)` | 实现 `run(ctx: StepContext) -> StepResult` |
| PipelineEngine 扩展 | 为 BaseTestCase 添加适配层（如 `_is_base_test_case()`） | 保持引擎只识别原生 Action 接口 |
| 日志上报 | `_maybe_send_heartbeat()`、HTTP 心跳 | `ctx.logger.info(...)` → Redis Streams → WebSocket |
| 跨 Run 状态持久化 | `_log_buffer`、本地 JSON 文件 | `ctx.local_db`（SQLite WAL） |
| 新增 import | `from backend.agent.test_framework import ...` | 无（该模块仅保留不删除，不可引用） |

### Tool Action 标准接口

所有 `tool:<id>` 脚本统一实现以下接口，不得使用其他模式：

```python
from backend.agent.pipeline_engine import StepContext, StepResult

class MyAction:
    def run(self, ctx: StepContext) -> StepResult:
        ctx.logger.info("开始执行")
        # ctx.adb      — AdbWrapper
        # ctx.serial   — 设备序列号
        # ctx.params   — pipeline_def 传入的参数
        # ctx.shared   — 跨 Step 共享（同一 Run 内）
        # ctx.local_db — 跨 Run 持久化（Agent SQLite WAL）
        return StepResult(success=True)
```

### 违规识别

代码审查和 AI 辅助时，出现以下任意一项即视为违规，须立即修正：

- 类定义中出现 `BaseTestCase` 作为基类
- `PipelineEngine` 中出现 `_is_base_test_case` / `_run_base_test_case` / `step_id_str` 注入
- 新增 `from backend.agent.test_framework import` 语句
- `StepContext` 中出现仅服务于 BaseTestCase 桥接的额外字段
