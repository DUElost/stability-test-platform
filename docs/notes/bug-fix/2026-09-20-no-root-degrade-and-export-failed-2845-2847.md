# 无共享存储根的 Agent 启动降级 + 导出报告 Failed devices 口径（#2845 / #2847）

Status: implemented
Class: bug-fix

## Decision

### #2845 · EventUploader 无根即崩

问题前提复核（逐处实读）：`bootstrap_subsystems.py:63` 无条件
`EventUploader.instance().configure(...)`（隔壁 `LocalDiskMonitor` 在 `if cifs_root:` 里，
见第 69 行）；`event_uploader.py:207` 的
`self._nfs_root = nfs_root or str(get_aee_nfs_root())` 无条件求值，而
`aee/paths.py:30-35` 在 `STP_AEE_NFS_ROOT` 为空时抛
`RuntimeError("STP_AEE_NFS_ROOT is not set")`。该访问器在 `backend/agent/` 生产代码里
只有这一处调用（`grep -rn "get_aee_nfs_root()"` 证实），坏点唯一。

两层修：

1. **组件级（本质）**：共享存储根缺失按「未配置」处理，而不是抛错。该类文档化契约本
   就是「未配置 api_url/agent_secret/host_id 时组件自然 no-op」——根缺失是同一类输入
   缺失。`configure()` 捕获 `RuntimeError` → warning 一条、`_nfs_root=""`、
   `_configured=False`，`enqueue_local_event` 既有的 `not self._configured` 守卫随即生效。
   **不能只把根留空照常启动**：上送目的地是 `{root}/devices/…`
   （`event_uploader.py:413`、`:417`），空根会退化成进程 CWD 的相对路径。
2. **调用点（结构）**：bootstrap 与隔壁 spill monitor 用同一判据 `if cifs_root:` 门控，并把
   已解析的根显式传给 `configure(nfs_root=cifs_root)`，不再让组件自己二次读 env。

`reload_config`（`control_handler.py:190`）那条链保持原样：它正是「`.env` 重读后根才
出现」的补入口，`force=True` 重新求值即可从降级恢复。

### #2847 · 导出报告的 Failed devices 恒 0

`build_plan_run_export` 的 `summary` 从不写 `failed`，而 markdown 渲染
`summary.get("failed", 0)` → 恒 0（真失败数被消费端默认值吞掉）。补 `failed` 键，口径取
**FAILED + ABORTED**，与 `report_service.py:670` 的运行报告 `failed` 同口径（「未成功的
设备」= 不落在任何成功语义里）。逐状态计数仍在 markdown 正文逐行可见，合计不会藏数。

## Alternatives

- **#2845 只改 bootstrap（issue 的第一个建议）**：能止住启动崩，但 `reload_config` 那条链
  仍会在无根主机上抛（而那条链必须允许「根稍后出现」），组件契约与实现也继续背离。故采
  两层，组件级为主。
- **#2845 空根照常启动**：上送目录会落到进程 CWD 下（`Path("") / "devices"`），静默造出
  一个没人找得到的目录树。否决。
- **#2847 只计 FAILED**（对齐 `failed_job_count` / 前端「失败设备 N 台」）：与 PlanRun 模型
  一致，但「全部 ABORTED 的运行」会显示 `Failed devices: 0`——正是本 issue 要消灭的
  「看起来没事」形态（静默少报比多报危险）。故取 report_service 口径。
- **#2847 拆成 Failed/Aborted 两行**：超出本单范围，且逐状态行已承担该信息。

## Verification

反例优先（先证伪再采信）：用 `git show HEAD:<file>` 把三处生产改动临时退回后重跑新增/
加强的用例，**全部按预期红**：

- `test_configure_without_shared_root_degrades_to_noop` / `…recovers_when_shared_root_appears_later`
  → 旧代码 `RuntimeError: STP_AEE_NFS_ROOT is not set`
- `test_bootstrap_subsystems_736` 两条（`configure` 收不到 `nfs_root=` / 空根下仍调用
  `configure`）→ 红
- `test_export_summary_reports_failed_devices_2847` → `summary["failed"]` KeyError

恢复后（本机实际执行）：

- `env -i PATH=… PYTHONPATH=. python -m pytest backend/agent/tests/ -q` → **2206 passed**
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_plan_run_export.py backend/tests/api/test_plan_run_export.py -q`
  → **4 passed**
- 现场复现（无 `STP_AEE_NFS_ROOT` 的解释器）：`get_aee_nfs_root()` →
  `RuntimeError: STP_AEE_NFS_ROOT is not set`；同一进程修复后
  `EventUploader.configure(...)` → 警告一行 + `is_configured()=False`
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (12 gates)**

## Revisit

- **无根主机的可见面**：降级目前只留日志（`event_uploader_skipped cifs_root_empty` /
  `event_uploader_no_shared_root`）。这类主机的 `UPLOAD_PENDING` 事件会静默堆积，而心跳不
  回传共享存储状态、告警面也没有 upload 规则。若现场确有无根主机在用，需要一条可见面
  （指标或心跳字段）——本单不扩范围。
- **#2847 的 ABORTED 口径**：若产品最终定为「Failed 只数 FAILED」，改一行即可；本次取
  report_service 口径的理由见上。
- 未涉及 `ArtifactUploader` / `LogWatcherManager`：前者 `aee_shared_root=""` 本就可空，后者
  空根即「禁用 puller，仅记元数据」（5B1+D1），均为有意设计。
