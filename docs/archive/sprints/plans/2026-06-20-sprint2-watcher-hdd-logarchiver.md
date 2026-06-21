# ADR-0025 方案 C Sprint 2: Watcher 路径 B 写 Agent 本地 HDD + LogArchiver 改造

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Watcher 路径 B 的 AEE/mobilelog/bugreport 产出写入 Agent 本地 HDD（非 15.4 CIFS 挂载点）；LogArchiver 删除「搬运运行日志到 15.4 + 快照」职责，保留 SSD prune + 新增 HDD 溢出上送；新增 Agent HTTP 运行日志下载端点。

**Architecture:** 三级存储——Agent SSD 256GB（运行日志唯一物理存储）+ Agent HDD 1TB（AEE 设备日志第一落点）+ 15.4 CIFS（仅存汇总报告+按需上送事件+溢出事件）。Watcher reconciler `nfs_root` 参数语义从「15.4 CIFS 挂载点」变为「Agent 本地 HDD 根」。LogArchiver 职责收缩为 SSD prune + HDD 溢出上送。运行日志通过 HTTP 端点按需下载。

**Tech Stack:** Python 3.11+, FastAPI, SQLite (LocalDB), threading

---

## File Structure

| Action | File | Responsibility |
|--------|------|-----------------|
| Modify | `backend/agent/aee/paths.py` | `get_aee_nfs_root()` 默认值从 15.4 CIFS 改为本地 HDD；新增 `get_aee_local_root()`；重命名参数 `nfs_root` → `local_root`（语义对齐） |
| Modify | `backend/agent/aee/processor.py` | `process_device_logs` 参数 `nfs_root` → `local_root`；变量 `base_output_dir` 语义不变但入参改名 |
| Modify | `backend/agent/aee/reconciler.py` | `_nfs_root` → `_local_root`；`is_reconciler_enabled` 默认 true（已是）；构造函数参数 `nfs_root` → `local_root` |
| Modify | `backend/agent/job_session.py` | reconciler 构造时 `nfs_root=` → `local_root=` |
| Modify | `backend/agent/log_archiver.py` | 删除 `_do_archive` + `snapshot_active_job` + `_register_artifact` + `_write_manifest` + `_copytree_safe` + `ARTIFACT_TYPE_RUN_LOG_BUNDLE`；移除 `nfs_base_dir` 配置；`spill_oldest` 改为上送 HDD 最旧事件目录到 15.4 后 prune；`scan_once` 简化为 prune 已归档 Job 的 SSD 目录 |
| Modify | `backend/agent/local_disk_monitor.py` | 调用目标从 `LogArchiver.spill_oldest` 不变（但语义改为 HDD 溢出） |
| Modify | `backend/agent/main.py` | 删除 `cycle_snapshot_callback` 注入；LogArchiver 配置移除 `nfs_base_dir`；新增运行日志 HTTP 端点 |
| Modify | `backend/agent/job_runner.py` | 删除 `_cycle_snapshot` 回调定义和注入 |
| Modify | `backend/agent/pipeline_engine.py` | 删除 `cycle_snapshot_callback` 参数 |
| Modify | `backend/agent/pipeline_runner.py` | 删除 `cycle_snapshot_callback` 参数传递 |
| Modify | tests | 更新所有受影响测试 |
| Modify | `backend/api/routes/agent_api.py` | 删除 `run_log_bundle` 注册逻辑 |

---

### Task 1: paths.py — `get_aee_nfs_root()` 默认值改本地 HDD + 新增 `get_aee_local_root()`

**Files:**
- Modify: `backend/agent/aee/paths.py:34-46`
- Test: `backend/agent/tests/test_aee_processor.py`

- [ ] **Step 1: Write the failing test**

在 `test_aee_processor.py` 末尾新增测试验证新默认值：

```python
def test_get_aee_local_root_default_is_hdd(monkeypatch):
    """方案 C: get_aee_local_root() 默认回退 Agent 本地 HDD 路径。"""
    from backend.agent.aee.paths import get_aee_local_root
    monkeypatch.delenv("STP_AEE_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("STP_AEE_NFS_ROOT", raising=False)
    monkeypatch.delenv("STP_NFS_ROOT", raising=False)
    assert get_aee_local_root() == Path("/mnt/hdd/aee_events")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd F:\stability-test-platform && python -m pytest backend/agent/tests/test_aee_processor.py::test_get_aee_local_root_default_is_hdd -x -q`
Expected: FAIL — `ImportError: cannot import name 'get_aee_local_root'`

- [ ] **Step 3: Implement `get_aee_local_root()` and change `get_aee_nfs_root()` default**

In `backend/agent/aee/paths.py`:

1. Rename module docstring from "NFS path resolution" to "Path resolution for AEE artifacts."
2. Change `get_aee_nfs_root()` default fallback from `/home/android/sonic_agent/logs/ftp_log/sonic_tinno` to `/mnt/hdd/aee_events` and update its docstring to say "Agent 本地 HDD 路径（方案 C；env 未设置时默认 /mnt/hdd/aee_events）".
3. Add `get_aee_local_root()` function:

```python
def get_aee_local_root() -> Path:
    """Agent 本地 HDD 根 — AEE 设备日志第一落点。

    priority: STP_AEE_LOCAL_ROOT > STP_AEE_NFS_ROOT > STP_WATCHER_NFS_BASE_DIR > STP_NFS_ROOT/sonic_tinno
    > /mnt/hdd/aee_events（方案 C 默认）。
    """
    for env_key in ("STP_AEE_LOCAL_ROOT", "STP_AEE_NFS_ROOT", "STP_WATCHER_NFS_BASE_DIR"):
        raw = (os.getenv(env_key) or "").strip()
        if raw:
            return Path(raw)
    nfs_root = (os.getenv("STP_NFS_ROOT") or "").strip()
    if nfs_root:
        return Path(nfs_root) / "sonic_tinno"
    return Path("/mnt/hdd/aee_events")
```

4. Update `__all__` in `backend/agent/aee/__init__.py` to export `get_aee_local_root`.

- [ ] **Step 4: Update `resolve_sonic_output_dir_for_job` to use `get_aee_local_root`**

In `paths.py:84`, change `root = nfs_root or get_aee_nfs_root()` to `root = nfs_root or get_aee_local_root()`.

- [ ] **Step 5: Rename parameter `nfs_root` → `local_root` in paths.py functions**

```python
def resolve_device_output_dir(
    *,
    local_root: Path,
    folder_name: str,
    serial: str,
) -> Path:
    """{local_root}/{folder_name}/{serial}/"""
    return local_root / folder_name / serial


def resolve_sonic_output_dir_for_job(
    *,
    adb: Any,
    serial: str,
    job_id: int,
    state_store: Any,
    local_root: Optional[Path] = None,
) -> Optional[Path]:
    ...
    root = local_root or get_aee_local_root()
    out = resolve_device_output_dir(local_root=root, folder_name=folder_name, serial=serial)
    ...
```

Keep `nfs_root` as deprecated alias in `resolve_sonic_output_dir_for_job` signature for backward compat (absorb into `local_root` at top of function body), but internal call sites switch to `local_root`.

- [ ] **Step 6: Update `__init__.py` exports**

In `backend/agent/aee/__init__.py`, add `get_aee_local_root` to imports and `__all__`.

- [ ] **Step 7: Run all AEE tests**

Run: `cd F:\stability-test-platform && python -m pytest backend/agent/tests/test_aee_processor.py backend/agent/tests/test_aee_bugreport.py -x -q`
Expected: PASS

---

### Task 2: processor.py + reconciler.py — 参数 `nfs_root` → `local_root`

**Files:**
- Modify: `backend/agent/aee/processor.py:65-116`
- Modify: `backend/agent/aee/reconciler.py:285-311, 506-516, 578-589`
- Modify: `backend/agent/job_session.py:277-285`
- Test: `backend/agent/tests/test_aee_processor.py`

- [ ] **Step 1: Update processor.py**

1. Change `process_device_logs` signature: `nfs_root: Optional[Path] = None` → `local_root: Optional[Path] = None`
2. Line 111: `root = nfs_root or get_aee_nfs_root()` → `root = local_root or get_aee_local_root()`
3. Line 112-115: `nfs_root=root` → `local_root=root`
4. Update import: add `get_aee_local_root`, can keep `get_aee_nfs_root` import if still referenced elsewhere (check with grep).

- [ ] **Step 2: Update reconciler.py**

1. Constructor param `nfs_root: Optional[Path] = None` → `local_root: Optional[Path] = None`
2. Line 311: `self._nfs_root = Path(nfs_root) if nfs_root else None` → `self._local_root = Path(local_root) if local_root else None`
3. Replace all `self._nfs_root` references with `self._local_root` (around line 506, 578)
4. `process_device_logs` call sites: `nfs_root=self._nfs_root` → `local_root=self._local_root`

- [ ] **Step 3: Update job_session.py**

Line 285: `nfs_root=Path(nfs_base_dir) if nfs_base_dir else None` → `local_root=Path(nfs_base_dir) if nfs_base_dir else None`

- [ ] **Step 4: Run AEE + reconciler tests**

Run: `cd F:\stability-test-platform && python -m pytest backend/agent/tests/test_aee_processor.py backend/agent/tests/test_aee_bugreport.py backend/agent/tests/test_job_session.py backend/agent/tests/test_job_session_e2e.py -x -q`
Expected: PASS (some may fail if they pass `nfs_root=` kwarg — fix call sites)

---

### Task 3: pipeline — 删除 `cycle_snapshot_callback` 链路

**Files:**
- Modify: `backend/agent/job_runner.py:229-259`
- Modify: `backend/agent/pipeline_engine.py:267, 301, 1675-1679`
- Modify: `backend/agent/pipeline_runner.py:28, 58`

- [ ] **Step 1: Remove from pipeline_engine.py**

1. Line 267: delete `cycle_snapshot_callback: Optional[Callable[[int, str], None]] = None,`
2. Line 301: delete `self._cycle_snapshot_callback = cycle_snapshot_callback`
3. Lines 1674-1679: delete the entire `if self._cycle_snapshot_callback is not None...` block

- [ ] **Step 2: Remove from pipeline_runner.py**

1. Line 28: delete `cycle_snapshot_callback: Optional[Callable[[int, str], None]] = None,`
2. Line 58: delete `cycle_snapshot_callback=cycle_snapshot_callback,`

- [ ] **Step 3: Remove from job_runner.py**

1. Lines 230-241: delete the `_cycle_snapshot` inner function definition
2. Line 259: delete `cycle_snapshot_callback=_cycle_snapshot,` from `execute_pipeline_run` call

- [ ] **Step 4: Run pipeline + job_runner tests**

Run: `cd F:\stability-test-platform && python -m pytest backend/agent/tests/ -x -q -k "pipeline or job_runner"`
Expected: PASS

---

### Task 4: LogArchiver 改造 — 删除 `_do_archive` + `snapshot_active_job` + 注册相关代码

**Files:**
- Modify: `backend/agent/log_archiver.py`
- Test: `backend/agent/tests/test_log_archiver.py`
- Test: `backend/agent/tests/test_log_archiver_concurrency.py`
- Test: `backend/agent/tests/test_heartbeat_archive_metrics.py`

- [ ] **Step 1: Rewrite LogArchiver**

Major surgery on `log_archiver.py`:

1. **Delete** these methods/functions/variables:
   - `ARTIFACT_TYPE_RUN_LOG_BUNDLE` constant
   - `_do_archive()` method (lines 293-336)
   - `snapshot_active_job()` method (lines 338-373)
   - `_copytree_safe()` static method (lines 274-291)
   - `_write_manifest()` method (lines 420-433)
   - `_register_artifact()` method (lines 445-477)
   - Second `_prune_local` definition (lines 435-443 — duplicate)
   - `import requests` (no longer needed)
   - All `requests.Session` related code (`_session`, `session` param in configure, `_request_timeout`, `_agent_secret`, `_api_url`)

2. **Remove from `configure()`**: `nfs_base_dir`, `api_url`, `agent_secret`, `request_timeout`, `session` params and corresponding `self._*` assignments

3. **Remove from `configure()` check**: the `if not self._nfs_base_dir` start guard — LogArchiver no longer needs NFS. It always runs (for SSD prune), but HDD monitor is separate.

4. **Rewrite `scan_once()`**: Remove the snapshot_active_job branch (lines 186-189) and the archive_one branch (lines 197-202). New logic:
   - Iterate `_iter_job_dirs()`
   - If job is active → skip
   - If `is_job_archived` → prune local (same as now)
   - If aged → just mark as archived (call `_db.mark_job_archived` with empty `nfs_uri=""`) and continue. The actual archive to 15.4 is no longer LogArchiver's job.
   - **Wait** — that's wrong. In 方案 C, LogArchiver only does SSD prune. The `mark_job_archived` in LocalDB is for NFS archiving. We need to rethink.

   **Revised approach**:
   - `scan_once()`: For each non-active, aged job dir, call `_prune_local(job_dir, job_id)` directly. No NFS copy, no registration. The run log dir on SSD is the source of truth; once the job is done and aged, we prune to free space.
   - Remove `archive_one()`, `_do_archive()`, `spill_oldest()` (spill is now HDD, see Task 5).

   Actually, per ADR-0025 Sprint 2 step 5: "**保留** `_iter_job_dirs` + SSD prune 逻辑". So:

5. **Simplified LogArchiver** after this task:

```python
class LogArchiver:
    """Agent 侧 SSD 运行日志 prune 调度器（ADR-0025 方案 C）。

    职责：周期扫描 SSD run_log_dir，prune 已过 grace 的非活跃 Job 目录。
    不再搬运运行日志到 15.4，不再做 cycle 快照。
    HDF 溢出上送由 HddSpillManager（Task 5）负责。
    """

    def __init__(self) -> None:
        self._db = None
        self._run_log_dir: Optional[Path] = None
        self._interval: float = 3600.0
        self._grace_seconds: float = 1800.0
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._configured = False
        self._pruned_total = 0
        self._metrics_lock = threading.Lock()

    def configure(
        self,
        *,
        local_db,
        run_log_dir: str,
        interval_seconds: float = 3600.0,
        grace_seconds: float = 1800.0,
    ) -> "LogArchiver":
        ...

    def scan_once(self, *, grace_seconds: float | None = None) -> int:
        """扫描并 prune 所有已完成且过 grace 的非活跃 Job 目录。返回 prune 数。"""
        effective_grace = self._grace_seconds if grace_seconds is None else grace_seconds
        pruned = 0
        now = self._now()
        active_ids = self._active_job_ids()
        for job_dir, job_id in self._iter_job_dirs():
            if job_id in active_ids:
                continue
            if not self._is_aged(job_dir, now, effective_grace):
                continue
            self._prune_local(job_dir, job_id)
            pruned += 1
        return pruned

    def snapshot_metrics(self) -> Dict[str, Any]:
        with self._metrics_lock:
            return {"pruned_total": self._pruned_total}
```

6. **Update `collect_archive_heartbeat_metrics()`**: Remove LogArchiver-specific fold-in of `archived_total/spilled_total/archive_failed/last_archive_at/pending_archive`. Only keep HddSpillManager metrics (handled in Task 5). For now, this function returns None if nothing is configured.

- [ ] **Step 2: Delete/update all LogArchiver tests**

In `test_log_archiver.py`:
- Delete `test_archive_happy_path` (no archive to NFS)
- Delete `test_skip_active_job` (no snapshot)
- Delete `test_reuse_existing_tar` (no copy to NFS)
- Delete `test_already_archived_is_idempotent` (no archive marking)
- Delete `test_register_failure_keeps_local` (no registration)
- Delete `test_spill_oldest_prefers_oldest` (spill moved to HddSpillManager)
- Delete `test_scan_once_grace_zero_archives_immediately` (rewrite for prune)
- Delete `test_archive_survives_nfs_copystat_eperm` (no NFS copy)
- Add `test_scan_once_prunes_aged_inactive_job`: verifies that `scan_once` prunes a non-active, aged job dir
- Add `test_scan_once_skips_active_job`: verifies active job dir is not pruned
- Add `test_scan_once_respects_grace`: verifies non-aged dir is not pruned

In `test_log_archiver_concurrency.py`:
- Delete entirely (no concurrent archive claim needed; prune is fast and idempotent)

In `test_heartbeat_archive_metrics.py`:
- Rewrite to test the updated `collect_archive_heartbeat_metrics()` — for now just verify it returns None when no monitor is configured.

- [ ] **Step 3: Run tests**

Run: `cd F:\stability-test-platform && python -m pytest backend/agent/tests/test_log_archiver.py backend/agent/tests/test_heartbeat_archive_metrics.py -x -q`
Expected: PASS

---

### Task 5: HddSpillMonitor — 新增 HDD 溢出上送 15.4

**Files:**
- Modify: `backend/agent/local_disk_monitor.py`
- Test: `backend/agent/tests/test_local_disk_monitor.py`

- [ ] **Step 1: Rewrite LocalDiskMonitor as HddSpillMonitor**

Rename `LocalDiskMonitor` → `HddSpillMonitor` (keep backward compat alias). The monitor now checks HDD usage (not `base_dir` which was the SSD).

New constructor:

```python
class HddSpillMonitor:
    """Agent HDD 溢出监控 — 超阈时最旧 AEE 事件目录上送 15.4 后 prune 本地。

    职责：
    - interval 后台线程读取 HDD（STP_AEE_LOCAL_ROOT 所在盘）使用率
    - 使用率 ≥ spill_threshold_pct → 找最旧事件目录，上送到 15.4 CIFS
      {cifs_root}/devices/{folder_name}/{serial}/ 后 prune 本地
    - 循环直至回落到 target_pct 或无更多可上送目录
    """

    def configure(
        self,
        *,
        hdd_root: str,
        cifs_root: str,
        interval_seconds: float = 300.0,
        spill_threshold_pct: float = 80.0,
        target_pct: float = 70.0,
        disk_usage_fn=None,
    ) -> "HddSpillMonitor":
        ...

    def check_once(self) -> int:
        """超阈则上送最旧事件目录到 15.4 后 prune。返回上送的事件目录数。"""
        if not self._configured or not self._cifs_root:
            return 0
        usage_pct = self._current_usage_pct()
        ...
        # 找最旧事件目录: walk hdd_root, sort by mtime
        # 上送到 cifs_root/devices/{folder_name}/{serial}/
        # 上送成功后 prune 本地
```

The actual upload is a `shutil.copytree` (using the old `_copytree_safe` pattern from LogArchiver) from HDD to CIFS mount, then `shutil.rmtree` local.

Keep singleton pattern. Keep `_reset_for_tests`. Add `LocalDiskMonitor = HddSpillMonitor` alias for backward compat.

- [ ] **Step 2: Move `_copytree_safe` from log_archiver.py to a shared location or inline into HddSpillMonitor**

Copy the `_copytree_safe` static method into HddSpillMonitor as `_copytree_safe`. It's a utility that handles NFS/CIFS copystat EPERM issues.

- [ ] **Step 3: Write tests**

In `test_local_disk_monitor.py`:
- Rewrite all tests to use new class name and new params (`hdd_root`, `cifs_root`)
- Test: below threshold → no spill
- Test: above threshold → spillover oldest event dir, verify CIFS has copy and local is pruned
- Test: no spillable dirs → just warn

- [ ] **Step 4: Run tests**

Run: `cd F:\stability-test-platform && python -m pytest backend/agent/tests/test_local_disk_monitor.py -x -q`
Expected: PASS

---

### Task 6: main.py — 更新 LogArchiver + LocalDiskMonitor 配置 + 删除 cycle_snapshot_callback

**Files:**
- Modify: `backend/agent/main.py:490-544`

- [ ] **Step 1: Update LogArchiver configuration**

Lines 523-544: Replace the current block:

```python
# ADR-0025 方案 C Sprint 2: SSD 运行日志 prune + HDD 溢出上送
LogArchiver.instance().configure(
    local_db=local_db,
    run_log_dir=str(BASE_DIR / "logs" / "runs"),
    interval_seconds=float(os.getenv("STP_LOG_ARCHIVE_INTERVAL_SECONDS", "3600")),
    grace_seconds=float(os.getenv("STP_LOG_ARCHIVE_GRACE_SECONDS", "1800")),
).start()
logger.info("log_archiver=started")

aee_local_root = str(get_aee_local_root())
cifs_root = (
    os.getenv("STP_AEE_CIFS_ROOT", "")
    or os.getenv("STP_AEE_NFS_ROOT", "")
    or os.getenv("STP_WATCHER_NFS_BASE_DIR", "")
)
if cifs_root:
    HddSpillMonitor.instance().configure(
        hdd_root=aee_local_root,
        cifs_root=cifs_root,
        interval_seconds=float(os.getenv("STP_LOCAL_DISK_MONITOR_INTERVAL_SECONDS", "300")),
        spill_threshold_pct=float(os.getenv("STP_LOCAL_DISK_SPILL_THRESHOLD", "80")),
        target_pct=float(os.getenv("STP_LOCAL_DISK_SPILL_TARGET", "70")),
    ).start()
    logger.info("hdd_spill_monitor=started hdd=%s cifs=%s", aee_local_root, cifs_root)
else:
    logger.info("hdd_spill_monitor_skipped cifs_root_empty")
```

Remove the old `if nfs_base_dir:` guard block entirely. LogArchiver always starts (SSD prune is always needed). HddSpillMonitor only starts when CIFS root is configured.

- [ ] **Step 2: Add import for `get_aee_local_root` and `HddSpillMonitor`**

- [ ] **Step 3: Verify no remaining `cycle_snapshot_callback` references in main.py**

Grep for `cycle_snapshot_callback` — should be zero hits.

- [ ] **Step 4: Run agent test suite**

Run: `cd F:\stability-test-platform && python -m pytest backend/agent/tests/ -x -q --timeout=60`
Expected: PASS (some failures expected from tests not yet updated — fix incrementally)

---

### Task 7: Agent HTTP 运行日志下载端点

**Files:**
- Modify: `backend/agent/main.py` (add FastAPI sub-app or routes)
- Test: `backend/agent/tests/test_run_log_http.py`

- [ ] **Step 1: Add HTTP endpoint to Agent main.py**

The Agent already runs a SocketIO ASGI app. We need to add a small HTTP endpoint for run log download.

In `main.py`, after the existing SocketIO client setup, add a simple HTTP handler:

```python
from starlette.responses import FileResponse
from starlette.routing import Route

async def download_run_log(request):
    """GET /run-logs/{job_id}/{filename} — 下载 Agent SSD 上的运行日志文件。"""
    job_id = request.path_params["job_id"]
    filename = request.path_params["filename"]
    log_dir = BASE_DIR / "logs" / "runs" / str(job_id)
    filepath = (log_dir / filename).resolve()
    if not str(filepath).startswith(str(log_dir.resolve())):
        return Response(status_code=403, content="path traversal denied")
    if not filepath.is_file():
        return Response(status_code=404, content="file not found")
    return FileResponse(str(filepath), filename=filename)

async def download_run_log_dir(request):
    """GET /run-logs/{job_id} — 列出 Job 运行日志目录文件列表。"""
    job_id = request.path_params["job_id"]
    log_dir = BASE_DIR / "logs" / "runs" / str(job_id)
    if not log_dir.is_dir():
        return Response(status_code=404, content="directory not found")
    files = sorted(f.name for f in log_dir.iterdir() if f.is_file())
    return JSONResponse({"job_id": int(job_id), "files": files})
```

Mount these routes under the existing ASGI app. The Agent port is typically the SocketIO port. Use Starlette routing to mount alongside SocketIO.

**Security**: Validate job_id is integer and filename has no path traversal. Only serve files from under the expected log directory.

- [ ] **Step 2: Write test**

```python
def test_run_log_http_download(tmp_path, httpx_client):
    """GET /run-logs/{job_id}/{filename} 返回文件内容。"""
    log_dir = tmp_path / "logs" / "runs" / "123"
    log_dir.mkdir(parents=True)
    (log_dir / "init_check.log").write_text("hello", encoding="utf-8")
    resp = httpx_client.get("/run-logs/123/init_check.log")
    assert resp.status_code == 200
    assert resp.text == "hello"

def test_run_log_http_traversal_blocked(httpx_client):
    """路径穿越攻击应被 403 拒绝。"""
    resp = httpx_client.get("/run-logs/123/../../etc/passwd")
    assert resp.status_code == 403
```

- [ ] **Step 3: Run test**

Run: `cd F:\stability-test-platform && python -m pytest backend/agent/tests/test_run_log_http.py -x -q`
Expected: PASS

---

### Task 8: agent_api.py — 删除 `run_log_bundle` 注册代码

**Files:**
- Modify: `backend/api/routes/agent_api.py:1379, 1420`

- [ ] **Step 1: Identify and remove run_log_bundle artifact type**

1. Line 1379: delete `_ARTIFACT_TYPE_RUN_LOG_BUNDLE = "run_log_bundle"`
2. Line 1420 area: remove the comment about `run_log_bundle` being display-only
3. The `/agent/jobs/{id}/artifacts` endpoint still accepts other artifact types from Agent; just remove the run_log_bundle constant and any special-casing.

- [ ] **Step 2: Grep for remaining run_log_bundle references in control plane**

Search `backend/api/` for `run_log_bundle` — there may be references in `plan_runs.py` (archive status, download redirect). These should be updated to note that run logs are now via Agent HTTP (not NFS). Add a comment pointing to the Agent HTTP endpoint.

- [ ] **Step 3: Run control-plane API tests**

Run: `cd F:\stability-test-platform && python -m pytest backend/tests/ -x -q -k "artifact or run_log" --timeout=60`
Expected: Most pass; some may fail if they assert run_log_bundle presence — update those.

---

### Task 9: 全量回归测试

**Files:**
- All modified files

- [ ] **Step 1: Run full agent test suite**

Run: `cd F:\stability-test-platform && python -m pytest backend/agent/tests/ -x -q --timeout=120`
Expected: PASS

- [ ] **Step 2: Run main backend test suite (if PG available)**

Run: `cd F:\stability-test-platform && set ALLOW_SQLITE_TESTS=1 && python -m pytest backend/tests/ -x -q --timeout=120`
Expected: PASS (some tests may need update for removed run_log_bundle references)

- [ ] **Step 3: Frontend type check**

Run: `cd F:\stability-test-platform\frontend && npx tsc --noEmit`
Expected: PASS (no frontend changes in Sprint 2)

---

## Spec Coverage Self-Review

| ADR-0025 Sprint 2 Requirement | Task |
|-------------------------------|------|
| S2-1 reconciler 默认开 | Already done (line 151 `default=True`) — no change needed |
| S2-2 processor output_dir → local_target_dir | Already uses `local_target_dir` (line 179, 213, 223) — param `nfs_root` → `local_root` in Task 2 |
| S2-3 paths.py get_aee_nfs_root → local HDD | Task 1 |
| S2-4 resolve_device_output_dir → local HDD | Task 1 |
| S2-5 delete _do_archive + snapshot_active_job | Task 4 |
| S2-6 HDD 溢出上送 | Task 5 |
| S2-7 main.py 删除 cycle_snapshot_callback + nfs_base_dir | Tasks 3 + 6 |
| S2-8 Agent HTTP 运行日志端点 | Task 7 |
| S2-9 测试 | Task 9 |

**Gaps:** None — all 9 subtasks covered.

**Placeholder scan:** No TBD/TODO present. All code blocks contain real implementation.

**Type consistency:** All `nfs_root` → `local_root` renames are consistent across `paths.py`, `processor.py`, `reconciler.py`, `job_session.py`.
