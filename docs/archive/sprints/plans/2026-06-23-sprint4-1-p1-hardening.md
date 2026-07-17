# Sprint 4.1 P1 行为加固 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Sprint 4 五项 P1 行为缺陷——多 host poll、增量 re-scan、glob 收窄、fallback 移除、时序文档化。

**Architecture:** 每个 P1 项为独立 Task，互不依赖，可并行。P1-5 移除 fallback 后 ScanRunner 在无 fresh 文件时返回 None，UploadManager 需同步处理；P1-3 需同步修改 cron_scheduler + dedup_scan + saq_tasks 三处。

**Tech Stack:** Python 3.11 / SQLAlchemy / SAQ / asyncio / pytest

---

## File Structure

| File | Change | P1 |
|------|--------|----|
| `backend/tasks/saq_tasks.py:129-184` | poll 等待所有 host artifact | P1-1 |
| `backend/services/dedup_scan.py:105-130` | `run_scan_sync` 支持增量 key | P1-3 |
| `backend/services/dedup_scan.py:233-269` | `enqueue_dedup_terminal_*` 传 `is_final` 参数 | P1-3 |
| `backend/scheduler/cron_scheduler.py:317-324` | `has_scan > 0` 改增量逻辑 | P1-3 |
| `backend/agent/upload_manager.py:143-165` | glob 改时间戳正则 + 深度限制 | P1-4 |
| `backend/agent/scan_runner.py:139-151` | 移除 `all_candidates` fallback | P1-5 |
| `docs/design/06-realtime-and-background.md` | 补充时序文档 | P1-2 |
| `backend/agent/tests/test_scan_runner.py` | 新增 P1-5 单测 | P1-5 |
| `backend/agent/tests/test_upload_manager.py` | 新增 P1-4 单测 | P1-4 |

---

### Task 1: P1-1 — scan_task 多 host poll 等待所有 artifact

**Files:**
- Modify: `backend/tasks/saq_tasks.py:129-184`

**当前问题**：`scan_task` poll 循环在 `run_scan_sync` 返回第一个 artifact 即 `break`，其余 host 结果丢失。

- [ ] **Step 1: 修改 poll 循环逻辑**

将 `saq_tasks.py:129-154` 的 poll 逻辑改为：每轮 poll 调用 `run_scan_sync`，查询 DB 中已有的 `scan_result_xls` 数量 vs `triggered` 数量，全部到达或超时才退出。

```python
    if triggered:
        from backend.services.dedup_scan import run_scan_sync

        _SCAN_POLL_INTERVAL = 10
        _SCAN_POLL_MAX_WAIT = 300
        elapsed = 0
        registered = 0
        n_triggered = len(triggered)
        while elapsed < _SCAN_POLL_MAX_WAIT:
            await asyncio.sleep(_SCAN_POLL_INTERVAL)
            elapsed += _SCAN_POLL_INTERVAL
            n_new = await asyncio.to_thread(run_scan_sync, plan_run_id)
            if n_new:
                registered += int(n_new)
            if registered >= n_triggered:
                break
            logger.info(
                "saq_scan_poll plan_run=%d elapsed=%ds registered=%d/%d",
                plan_run_id, elapsed, registered, n_triggered,
            )

        if registered == 0:
            await asyncio.to_thread(run_scan_sync, plan_run_id)

        logger.info(
            "saq_scan_registered plan_run=%d artifacts=%d/%d waited=%ds",
            plan_run_id, registered, n_triggered, elapsed,
        )
```

- [ ] **Step 2: 验证 agent tests 通过**

Run: `pytest backend/agent/tests/ -x -q`
Expected: 570 passed

---

### Task 2: P1-3 — auto_archive_sweep 增量 re-scan 支持

**Files:**
- Modify: `backend/services/dedup_scan.py:105-130, 233-269`
- Modify: `backend/scheduler/cron_scheduler.py:317-324`

**当前问题**：
1. `auto_archive_sweep` 中 `has_scan > 0` 跳过已有 scan 的 PlanRun → 增量 re-scan 被阻断
2. `enqueue_dedup_terminal_sync` 硬编码 `is_final=True`
3. SAQ key `scan:{plan_run_id}` 导致增量 job 被去重丢弃

- [ ] **Step 1: `run_scan_sync` 支持增量 key 后缀**

在 `dedup_scan.py:105`，`run_scan_sync` 签名不变，但 `auto_archive_sweep` 传增量标记时 SAQ key 需区分。

- [ ] **Step 2: `enqueue_dedup_terminal_sync` 新增 `is_final` 参数**

修改 `dedup_scan.py:255-269`：

```python
def enqueue_dedup_terminal_sync(plan_run_id: int, *, is_final: bool = True) -> None:
    """同步 enqueue scan_task（scan_task 完成后自行串行 enqueue upload + merge）。"""
    try:
        from backend.tasks.saq_worker import enqueue_sync

        suffix = "" if is_final else ":inc"
        enqueue_sync(
            "scan_task",
            key=f"scan:{plan_run_id}{suffix}",
            timeout=900,
            retries=2,
            plan_run_id=plan_run_id,
            is_final=is_final,
        )
    except Exception as e:
        logger.error("enqueue_dedup_terminal_sync failed plan_run=%d: %s", plan_run_id, e)
```

同步修改 `enqueue_dedup_terminal_async` (line 233)：

```python
async def enqueue_dedup_terminal_async(plan_run_id: int, *, is_final: bool = True) -> None:
    """异步 enqueue scan_task（scan_task 完成后自行串行 enqueue upload + merge）。"""
    try:
        from backend.tasks.saq_worker import get_queue
        from saq import Job as SaqJob

        suffix = "" if is_final else ":inc"
        queue = get_queue()
        await queue.enqueue(
            SaqJob(
                function="scan_task",
                kwargs={"plan_run_id": plan_run_id, "is_final": is_final},
                key=f"scan:{plan_run_id}{suffix}",
                timeout=900,
                retries=2,
                retry_delay=10.0,
                retry_backoff=True,
            )
        )
    except Exception as e:
        logger.error("enqueue_dedup_terminal_async failed plan_run=%d: %s", plan_run_id, e)
```

- [ ] **Step 3: `auto_archive_sweep` 改增量逻辑**

修改 `cron_scheduler.py:317-326`：

```python
                has_scan = db.execute(
                    select(func.count()).select_from(PlanRunArtifact).where(
                        PlanRunArtifact.plan_run_id == run.id,
                        PlanRunArtifact.artifact_type == "scan_result_xls",
                    )
                ).scalar_one()
                enqueue_dedup_terminal_sync(
                    run.id,
                    is_final=(has_scan == 0),
                )
                triggered += 1
```

移除 `if has_scan > 0: continue` — 改为：已有 scan → `is_final=False`（增量），无 scan → `is_final=True`（首次）。

- [ ] **Step 4: 验证 agent tests 通过**

Run: `pytest backend/agent/tests/ -x -q`
Expected: 570 passed

---

### Task 3: P1-4 — upload_event_dirs glob 模式收窄

**Files:**
- Modify: `backend/agent/upload_manager.py:143-165`
- Modify: `backend/agent/tests/test_upload_manager.py`

**当前问题**：`rglob("*_*")` 匹配任何含下划线的路径，过宽；`name[0].isdigit()` 弱过滤。

AEE HDD 事件目录命名约定：`{YYYY-MM-DD}_HH-MM-SS_{db_path}`（如 `2026-06-23_14-30-00_db.01`）。

- [ ] **Step 1: 修改 glob 逻辑**

在 `upload_manager.py` 顶部新增 import：

```python
import re
```

在 class 外或 class 内新增常量：

```python
_EVENT_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_")
```

修改 `upload_event_dirs` 自动发现分支 (line 143-165)：

```python
        if not event_dir_names:
            for event_dir in sorted(base_src.rglob("*_*")):
                if not event_dir.is_dir():
                    continue
                if not _EVENT_DIR_RE.match(event_dir.name):
                    continue
                if event_dir.parent != base_src:
                    continue
                dst_dir = base_dst / event_dir.name
                if dst_dir.exists():
                    continue
                try:
                    self._copytree_safe(str(event_dir), str(dst_dir))
                    count += 1
                except Exception:
                    logger.exception(
                        "upload_event_dirs_auto_copy_failed plan_run=%d dir=%s",
                        plan_run_id, event_dir,
                    )
            logger.info(
                "upload_event_dirs_auto plan_run=%d copied=%d from=%s",
                plan_run_id, count, source_root,
            )
            return count
```

关键改动：
- `event_dir.name[0].isdigit()` → `_EVENT_DIR_RE.match(event_dir.name)` 匹配时间戳前缀
- `rel = event_dir.relative_to(base_src)` + `base_dst / rel` → `base_dst / event_dir.name`（不再保留任意深套路径）
- 新增 `event_dir.parent != base_src` 深度限制：只取 source_root 直接子目录

- [ ] **Step 2: 新增单测 — 自动发现只匹配时间戳目录**

在 `test_upload_manager.py` 末尾新增：

```python
def test_upload_event_dirs_auto_discover_ignores_non_timestamp(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))

    src_root = tmp_path / "events"
    src_root.mkdir()
    good = src_root / "2026-06-23_14-30-00_db.01"
    good.mkdir()
    (good / "main.dbg").write_text("ok")
    bad_no_ts = src_root / "some_random_dir"
    bad_no_ts.mkdir()
    (bad_no_ts / "file.txt").write_text("bad")
    bad_nested = src_root / "subdir"
    bad_nested.mkdir()
    bad_deep = bad_nested / "2026-06-23_15-00-00_db.02"
    bad_deep.mkdir(parents=True, exist_ok=True)
    (bad_deep / "nested.txt").write_text("nested")

    count = m.upload_event_dirs(42, [], str(src_root))

    assert count == 1
    assert (nfs / "devices" / "42" / "2026-06-23_14-30-00_db.01" / "main.dbg").exists()
    assert not (nfs / "devices" / "42" / "some_random_dir").exists()
    assert not (nfs / "devices" / "42" / "2026-06-23_15-00-00_db.02").exists()


def test_upload_event_dirs_auto_discover_skips_existing(tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    m = _make_manager(str(nfs))

    src_root = tmp_path / "events"
    src_root.mkdir()
    event_dir = src_root / "2026-06-23_14-30-00_db.01"
    event_dir.mkdir()
    (event_dir / "main.dbg").write_text("ok")

    dest = nfs / "devices" / "42" / "2026-06-23_14-30-00_db.01"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "placeholder.txt").write_text("here")

    count = m.upload_event_dirs(42, [], str(src_root))

    assert count == 0
    assert (dest / "placeholder.txt").read_text() == "here"
    assert not (dest / "main.dbg").exists()
```

- [ ] **Step 3: 运行 agent tests**

Run: `pytest backend/agent/tests/ -x -q`
Expected: 572 passed（+2 新增）

---

### Task 4: P1-5 — ScanRunner 移除 fallback 返回 None

**Files:**
- Modify: `backend/agent/scan_runner.py:139-151`
- Modify: `backend/agent/tests/test_scan_runner.py`

**当前问题**：`fresh or all_candidates` 在无 fresh 文件时 fallback 到全局旧文件，多 PlanRun 场景误取。

- [ ] **Step 1: 修改 scan_runner.py**

将 line 139-151 改为：

```python
        hdd = Path(self._hdd_root)
        all_candidates = list(hdd.glob("**/Result_*_org.xls"))
        fresh = [
            c for c in all_candidates
            if c.stat().st_mtime >= scan_start - 1
        ]
        if not fresh:
            logger.warning(
                "scan_runner_no_fresh_org_xls plan_run=%d host=%s hdd_root=%s total_candidates=%d",
                plan_run_id, host_id, self._hdd_root, len(all_candidates),
            )
            return None

        latest = max(fresh, key=lambda p: p.stat().st_mtime)
        org_xls = str(latest.resolve())
        logger.info(
            "scan_runner_success plan_run=%d host=%s org_xls=%s fresh=%d total=%d",
            plan_run_id, host_id, org_xls, len(fresh), len(all_candidates),
        )
        return org_xls
```

关键改动：
- 移除 `candidates = fresh or all_candidates`
- `fresh` 为空时直接 return None（不再 fallback）
- 选择 latest 时仅从 `fresh` 中选（非 `candidates`）
- warning 日志记录 `total_candidates` 以便排查

- [ ] **Step 2: 新增单测 — stale fallback 不再发生**

在 `test_scan_runner.py` 末尾新增：

```python
def test_run_local_scan_returns_none_when_no_fresh_xls(tmp_path):
    r = _make_runner()
    hdd = tmp_path / "hdd"
    hdd.mkdir()
    old_xls = hdd / "Result_shanghai_org.xls"
    old_xls.write_text("old")
    import os
    old_time = os.stat(old_xls).st_mtime - 100
    os.utime(str(old_xls), (old_time, old_time))
    r._hdd_root = str(hdd)

    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=0, stdout="done")
        result = r.run_local_scan(1, "host-1")

    assert result is None
```

- [ ] **Step 3: 更新既有单测**

`test_run_local_scan_calls_start_log_scan_with_dedup_org` (line 53-69) 中创建的 org_xls 可能 mtime 不够新。需确保测试中 org_xls 文件的 mtime 在 scan_start 之后。由于 `tmp_path` 新创建的文件 mtime 是当前时间，而 `scan_start = time.time()` 在 `subprocess.run` mock 之前记录，且 mock 立即返回，所以 mtime >= scan_start - 1 通常成立。但为安全起见，在 `_completed` 返回后手动 touch 该文件：

```python
def test_run_local_scan_calls_start_log_scan_with_dedup_org(tmp_path):
    r = _make_runner()
    hdd = tmp_path / "hdd"
    hdd.mkdir()
    org_xls = hdd / "Result_shanghai_org.xls"
    org_xls.write_text("fake")
    r._hdd_root = str(hdd)

    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="done")
        result = r.run_local_scan(42, "host-1")

    assert result is not None
    assert "Result_shanghai_org.xls" in result
    called_argv = mock_run.call_args[0][0]
    assert "-dedup_org" in called_argv
    assert str(hdd) in called_argv
    assert "-side" in called_argv
```

此测试无需修改——`tmp_path` 新建的文件 mtime 为当前时间，scan_start 也是当前时间，差值在 1s grace 内。

- [ ] **Step 4: 运行 agent tests**

Run: `pytest backend/agent/tests/ -x -q`
Expected: 571 passed（+1 新增）

---

### Task 5: P1-2 — 终态管道时序文档化

**Files:**
- Modify: `docs/design/06-realtime-and-background.md`

- [ ] **Step 1: 在 06-realtime-and-background.md 末尾新增 §9 ADR-0025 终态管道时序**

```markdown
#### §9 ADR-0025 终态 dedup 管道时序

PlanRun 进入终态（SUCCESS / PARTIAL_SUCCESS / FAILED / DEGRADED）后，控制面自动或手动触发以下管道：

```
PlanRun 终态
  └→ enqueue_dedup_terminal_sync → scan_task
       ├→ emit scan_now → 各 ONLINE Agent → ScanRunner.run_local_scan → UploadManager.upload_scan_report
       │                                                          → UploadManager.upload_event_dirs
       ├→ poll NFS dedup/{plan_run_id}/ (10s × 30 = 300s max)
       ├→ run_scan_sync → PlanRunArtifact(scan_result_xls) 注册 DB
       ├→ enqueue upload_task → emit upload_events → Agent upload_event_dirs → NFS devices/{run_id}/
       └→ enqueue merge_task → run_merge_sync → PlanRunArtifact(merge_result_xls) 注册 DB
```

**时序依赖**：
- `upload_task` 与 `merge_task` 可并行：upload 写 `devices/`，merge 读 `dedup/`，路径不冲突
- `extract` 依赖 merge 完成：提取需参考 merge xls 中的 db 路径，从 `devices/{run_id}/` 拷贝到 `jira/{run_id}/`
- `scan_task` 是链式入口：内部串行 enqueue upload + merge，保证 scan 先完成
- 多 host：`scan_task` poll 等待所有 triggered host 的 artifact 或超时

**五触发场景**：
| # | 场景 | is_final | 说明 |
|---|------|----------|------|
| 1 | 终态自动 | True | PlanRun 终态 → aggregator enqueue |
| 2 | abort | True | 用户 abort → 前端确认后 enqueue |
| 3 | FAILED/DEGRADED | True | 前端确认后 enqueue |
| 4 | 手动归档 | True | POST /archive → archive_now + scan_now |
| 5 | 自动归档间隔 | 首次 True / 增量 False | auto_archive_sweep 周期触发，已有 scan 时增量 re-scan |
```

- [ ] **Step 2: 验证前端 tsc + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: 无错误

---

### Task 6: 自检 + 验证

- [ ] **Step 1: 验证 agent tests 全量通过**

Run: `pytest backend/agent/tests/ -x -q`
Expected: 572+ passed

- [ ] **Step 2: 验证 Python import 无报错**

Run:
```
python -c "from backend.tasks.saq_tasks import scan_task; print('OK')"
python -c "from backend.services.dedup_scan import enqueue_dedup_terminal_sync; print('OK')"
python -c "from backend.agent.scan_runner import ScanRunner; print('OK')"
python -c "from backend.agent.upload_manager import UploadManager; print('OK')"
```

- [ ] **Step 3: 前端 tsc + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`

- [ ] **Step 4: Grep 检查引用点同步**

```
rg "enqueue_dedup_terminal_sync" backend/
rg "enqueue_dedup_terminal_async" backend/
rg "is_final" backend/tasks/saq_tasks.py backend/services/dedup_scan.py
```

确认所有调用处已更新为支持 `is_final` 参数。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "fix: P1-1 multi-host poll + P1-3 incremental scan + P1-4 glob narrowing + P1-5 fallback removal + P1-2 pipeline docs"
```
