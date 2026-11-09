# #3889：ScanRunner 扫描根委托正式解析器，与采集根同源

Status: implemented
Class: bug-fix

单点修复，不属于批次单元，不需要迁移；随 B8 联合 Agent 发布（见 #3800 评论
6705598818）滚动，本 PR 不部署、不激活、保持 Draft。

## Decision

[#3889](https://github.com/DUElost/stability-test-platform/issues/3889) 修正 Agent
安装布局（`/opt/stability-test-agent/agent`，无 `backend.` 包）下采集根与扫描根不一致：

- `backend/agent/scan_runner.py` 删除 `try: from backend.agent.aee.paths import
  get_aee_local_root / except ImportError: <本地重写>`。该绝对导入在安装布局下必然
  `ImportError`，回退实现只认 `STP_AEE_LOCAL_ROOT`、缺 SSD 回退，于是扫描根落到
  `/mnt/hdd/aee_events`，与正式解析器（`{LOG_DIR}/aee_events` 或
  `STP_AEE_SSD_FALLBACK_ROOT`）不一致。改为与同文件既有相对导入合并为一行
  `from .aee.paths import get_aee_local_root, get_aee_nfs_root`。修复后 Agent 中
  `get_aee_local_root` 只能由 `aee/paths.py` 实现。
- `backend/agent/bootstrap_subsystems.py` 把第 54 行已解析的 `hdd_root` 显式传给
  `ScanRunner.instance().configure(hdd_root=hdd_root)` 与
  `UnisocScanRunner.instance().configure(hdd_root=hdd_root)`，使同一次启动采集根与
  扫描根同源、`get_aee_local_root()` 只调用一次；不再让 `ScanRunner.configure()` 内部
  二次解析。
- 不改 `aee/paths.py` 解析链与默认值，不改 `.env.example` 与 `install_agent.sh`。

按 [#3800 规划者分诊 6731915893](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6731915893)，
有效的显式根（存在、可写、非 tmpfs）继续优先使用，行为与 main 一致。显式根不可用时，
扫描根与采集根统一走 `aee/paths.py` 的解析链，在 SSD 回退目录可写时一起回退到该目录。
这是本单要实现的行为。

停止条件核查（评论 6706036828）：除 `scan_runner.py` 外，安装布局下没有任何模块
解析出不同的 AEE 本地根。所有 `get_aee_local_root` 消费方（`bootstrap_subsystems`、
`event_uploader`、`job_session`、`system_monitor`、`aee/processor`、`aee/reconciler`、
`aee/unisoc_reconciler`）都经相对导入或双布局 `try/except` 指向同一个
`aee/paths.py`；生产代码中 `def get_aee_local_root` 仅 `scan_runner.py` 与
`aee/paths.py` 两处，硬编码 `/mnt/hdd/aee_events` 仅 `scan_runner.py`（缺陷）与
`aee/paths.py` 的 `DEFAULT`（解析链末级）。
`aee/scripts/push_resources/_nfs_root` 读的是 `STP_NFS_ROOT`（中心存储根，不同概念），
不是 AEE 本地根。故不触发「退回：#3889」，不扩大范围。

`UnisocScanRunner.configure` 说明：其签名含 `hdd_root` 形参（与 `ScanRunner.configure`
对齐），按规格「如果同样接受 hdd_root，也一并传入」故一并传入，**未扩改其接口**。但
该函数体 `del hdd_root, side` 会丢弃该值——Unisoc 的扫描根来自
`mtk_runner._prepare_scan_root(...)`（`mtk_runner = ScanRunner.instance()`），即复用
MTK `ScanRunner` 的 `_hdd_root`。因此本单对 Unisoc 的根正确性由 `ScanRunner` 的修复
传递达成，传入 `hdd_root` 是同源对齐、对 Unisoc 自身无副作用。

## Alternatives

| 选项 | 为何不选 |
|---|---|
| 保留双布局 `try/except`，只给本地回退补 SSD 回退 | 复制解析链，仍留两份 `get_aee_local_root`；规格要求「只能由 aee/paths.py 实现」，且同文件第 27 行已证明相对导入在两种布局下都可用 |
| 改 `aee/paths.py` 解析链或默认值放宽容错 | 会改变所有消费方语义；规格明令不改 |
| bootstrap 不传 hdd_root，仅依赖 scan_runner 委托正式解析器 | 采集根仍会在启动期被解析两次，未达成「只解析一次」；规格要求显式传入 |
| 给 UnisocScanRunner.configure 新增 hdd_root 使用逻辑 | 规格要求不扩改其接口；Unisoc 已由 MTK ScanRunner 同源派生扫描根 |

## Verification

反例先行：在**未修复的** `origin/main`（`3e605a1`）上先新增
`backend/agent/tests/test_scan_root_resolver_3889.py`，3 条回归测试失败、1 条
no-regression 守卫通过：

- 部署布局回归测试（子进程模拟 `sys.path` 仅含顶层 `agent` 包、`backend` 置 `None`，
  `STP_AEE_LOCAL_ROOT` 为空、SSD 回退目录可写）：失败证据
  `scan_root=/mnt/hdd/aee_events`，而 `canonical=<SSD 回退目录>`，与 #3889 现场一致。
- `bootstrap_subsystems` 测试以 `LocalDiskMonitor` 为采集侧对照。main 上扫描器的调用是
  `configure()`，测试期望它收到与 `LocalDiskMonitor` 相同的 `hdd_root`。
- 静态守卫：命中两处 `def get_aee_local_root`（`aee/paths.py:95`、
  `scan_runner.py:37`）。
- `STP_AEE_LOCAL_ROOT` 指向有效显式根（存在、可写、非 tmpfs）时，行为与 main 一致。
  该守卫在修复前后均通过。

应用修复后：`test_scan_root_resolver_3889.py` 与既有
`test_bootstrap_subsystems_736.py`（其 `configure()` 断言按新签名更新为
`configure(hdd_root=...)`）合计 **7 passed**。新测试覆盖：

- 安装布局 + 空 `STP_AEE_LOCAL_ROOT` + 可写 SSD 回退 → 扫描根 == 采集根 == SSD 目录，
  且 `scan_runner.get_aee_local_root is aee.paths.get_aee_local_root`；
- `STP_AEE_LOCAL_ROOT` 指向有效显式根（覆盖 15.101 类配置）→ 扫描根 == 配置根、优于 SSD 回退；
- bootstrap 一次解析、ScanRunner 与 `LocalDiskMonitor` 拿到同一 `hdd_root`、
  `UnisocScanRunner.configure` 同样收到；
- 静态守卫：`backend/agent` 生产代码（不含 tests）仅 `aee/paths.py` 定义
  `get_aee_local_root`；`scan_runner.py` 侧否定断言经
  `SourceGuard.of_module(...).anchored("from .aee.paths import get_aee_local_root")
  .assert_absent("def get_aee_local_root")`（#2639），避免锚点漂移时恒真。

复核者回退变异（把实现改回旧行为）可让 3 条回归测试再次失败；`docs/notes/` 不在
ADR-0059 写作约定适用范围。no-regression 守卫另有隔离变异自证：把
`aee/paths.py::get_aee_local_root` 的 `configured` 临时改为恒空（忽略
`STP_AEE_LOCAL_ROOT`），`test_configured_local_root_behavior_matches_main` 即失败
（`canonical=<SSD 回退>` ≠ 配置根），恢复 `aee/paths.py` 并清 pytest 断言重写缓存后
4 条全绿；`aee/paths.py` 相对 `origin/main` 无 diff。

- `pr-agent-tests` 等价全量 `backend/agent/tests/`（经 `scripts/run_pytest.py` 内存保护
  runner、`env -i` 干净环境）跑两轮：首轮 **3081 passed + 2 failed**，次轮
  **3082 passed + 1 failed**。两个失败都与本单无关：
  - `test_flash_preflight_v102::test_dialout_absent_and_no_rule_fails` 在 `origin/main`
    上**同样失败**（本机用户在 `dialout` 组，环境相关），两轮都稳定失败；
  - `test_scan_runner_idle_exit_race_1706::test_old_worker_exit_does_not_clear_restarted_worker_flag`
    是 `wait(5)` 线程时序用例，首轮全量负载下偶发失败、次轮通过，单跑及与本单新测试
    文件同跑 3/3 通过。本单只改 import 与 bootstrap 传参，未触碰 flash preflight
    或 worker 线程逻辑。
- `python scripts/run_gates.py check:quick`：**16 gates 全绿**（含 `env-inventory`、
  `layering`、`inner-imports`、`tool-manifest`、`transitions` 等静态门禁）。
  首轮曾因探针里的自造 env 读取触发 `env-inventory`，已改为把控制行注入探针源码，
  消除测试专用环境变量；`env_inventory.py --check` 复跑 `[OK]`（277 个读取名）。
- 原 Python 3.7 / 3.8 兼容要求作废。Agent venv 要求 Python ≥ 3.10，CI 的 Agent 测试运行在 Python 3.11。
- CI 修复（`pr-agent-tests` run 42519812106）：静态否定断言改为 `SourceGuard` 后，
  `tests/test_source_scan_anchor_ratchet.py` + `backend/agent/tests/test_scan_root_resolver_3889.py`
  **14 passed**；未改生产代码、未下调棘轮基线。

## Revisit

- 联合 Agent 发布滚动后，由部署实施者删除 15.43 的专用过度配置
  `STP_AEE_LOCAL_ROOT=/opt/stability-test-agent/logs/aee_events` 并重启，回到默认解析链，
  再按 #3889 的最小复现命令复验 `scan` 与 `collect` 同根（步骤写进 PR 正文「部署后」一节）。
- 若后续发现安装布局下还有模块自带 AEE 本地根解析（非 `aee/paths.py`），按评论
  6706036828 的停止条件在 #3800 发「退回：#3889」并附证据，不在本单扩大范围。
