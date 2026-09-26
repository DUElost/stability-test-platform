# ADR-0054 第 4 步（一）：`kernel_usb_faults` 解析/词表入契约包

Status: implemented
Class: architecture

## Decision

按 ADR-0054 §5 第 4 步「能抽出纯函数的抽」，把内核 USB 故障的**解析、词表与判定**收进
契约包（#3298），**采集留在 agent**：

| 归属 | 内容 |
|---|---|
| `backend/agent/contracts/kernel_usb_faults.py`（新） | 签名词表（`HC_DEAD_MARKER` / `HC_NOT_RESPONDING_MARKER` / `CABLE_SUSPECT_MARKER` / `LINK_ERROR_MARKERS`）、统计窗与阈值（`LINK_WINDOW_SECONDS` / `LINK_ERROR_THRESHOLD` / `CABLE_SUSPECT_THRESHOLD`）、`KernelUsbFaults`、`parse_kernel_usb_faults`、`usb_kernel_fault_reasons`、`REASON_*`、`CHANNEL_*`/`CHANNEL_STATES` |
| `backend/agent/kernel_usb_faults.py`（保留采集） | `SCAN_INTERVAL_SECONDS`、`_BLIND_HINT_MARKERS`、`_journal_env`、`kernel_log_is_readable`、`scan_kernel_usb_faults`、`KernelUsbWatch`（线程/节流/窗口）——ADR-0054 D2：运行逻辑不属于契约 |

具体改动：

- **C3 基线 2 → 1**：删 `backend.services.host_health_probe -> backend.agent.kernel_usb_faults`
  （探针改 import 契约）；剩余 1 条 `scripts.migrate_watcher_aee_state_keys ->
  agent.aee.state_migration` 是第 4 步的另一个候选项，**终态出口已写进 `.importlinter`
  注释**：该模块 stdlib-only（sqlite3/json）、内容以状态键格式 + 合并语义为主，按同一模式
  整体搬入 contracts 后删行；搬迁前保留基线，不算永久豁免。
- **控制面**：`host_health_probe.py` 改 `from backend.agent.contracts.kernel_usb_faults import
  parse_kernel_usb_faults`；新增单实现判据
  `test_journal_parsing_is_the_shared_contract_implementation`（防控制面长出私有解析）。
- **跨侧词表锁定的真值路径**：`tests/test_host_health_reason_surface.py` 的
  `KERNEL_USB_FAULTS` 改指契约文件——这个测试是 reason/channel 四处口径（agent 产出点 /
  控制面分桶 / 告警选择器 / 前端标签）的唯一结构守卫，带三条 `test_guard_detects_*` 判别力
  自证，本步只动真值路径、判据不变。
- **文案回扫**：`backend/core/metrics.py`、`backend/api/routes/metrics.py`（两处词表注释）、
  `deploy/prometheus/alerts-stability-platform.yml`（判据来源）、
  `docs/operations/host-device-visibility-triage.md`（L1 实现位置）改指契约；
  agent 侧 `heartbeat_thread` / agent 测试继续 import `backend.agent.kernel_usb_faults`
  （它们要的是扫描与监视线程）。

## Alternatives

- **整模块搬进契约**（连 journalctl 调用与线程一起）：弃——ADR-0054 D2 明文「采集、执行、
  状态迁移不属于契约」，且契约模块须能被控制面 import，带上 subprocess/thread 只会把运行
  逻辑搬进控制面进程。
- **只搬常量、解析留 agent**：弃——控制面探针真正需要的就是**同一解析签名**
  （`parse_kernel_usb_faults`）；只搬常量等于让两侧各自解析、对拍继续裸奔。
- **保留 C3 行不动（判「抽不出」）**：弃——本模块的解析与判定都是纯函数，属 ADR 明说
  「能抽出纯函数的抽」的一类；保留基线会让「控制面 import agent 内部」这条 C3 语义
  继续需要人工解释。
- **新增格式锚类似的 known-answer**：弃——本模块输出的不是身份摘要，而是计数/词表；
  离线回放素材（三例现场日志）已在 agent 测试里逐字固定，再加锚没有新信息。

## Verification

- `backend/agent/tests/` → **2187 passed**（4m08s，systemd-run 6G 硬顶；含 `test_kernel_usb_faults.py`
  的 58 例与 `test_capacity_reporter.py`）；
- 控制面与边界：`backend/tests/services/test_host_health_probe_2983.py`（含新单实现判据）、
  `backend/tests/api/test_metrics_host_health_gauges.py`、`tests/test_agent_import_boundary.py`、
  `tests/test_agent_test_import_ratchet.py` → **36 passed**；
- 跨侧词表守卫：`tests/test_host_health_reason_surface.py` + agent 解析/容量用例 → **91 passed**；
- `layering`（`--no-cache`）→ 5 合约全 KEPT，**C3 基线 2 → 1**；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (16 gates)**。
  （注：16 而非既往的 17，是 main 上 `chore(ADR-0051 D8)` 退役了 `new-script-family`
  门禁（b853634a），与本次改动无关。）
- **反向验证（临时变异，逐条复原）**：
  - 探针回落 `from backend.agent.kernel_usb_faults import …` → **`layering` C3 BROKEN**
    （基线行已删，实测）；同一变异下新的单实现判据**不红**——因为 agent 模块把
    契约函数对象原样带出，`is` 断言仍成立。两条退化路径的分工已写进该判据 docstring：
    私有重写 → 判据红；回落 import agent 内部 → C3 红。
  - 改契约签名（`HC_DEAD_MARKER = "hc dead"`）→ agent 解析用例红（离线回放判据有效）；
- 过程坑：agent 侧模块在包根（`backend/agent/kernel_usb_faults.py`），相对导入是
  `.contracts.kernel_usb_faults`（一个点）——首版写成 `..contracts` 会解析成
  `backend.contracts`，被 agent 套件收集期当场抓红。

## Revisit

- 第 4 步剩余：`aee/state_migration`（终态出口已写在 `.importlinter` C3 注释：整体搬入
  `contracts/` 后删最后 1 条基线）；
- `backend/agent/contracts/` 现 7 个模块；`kernel_usb_faults` 契约与采集的边界即
  ADR-0054 D2 的判据样板（「解析/词表进契约、I/O 与线程留 agent」），后续同类拆分照此；
- `scan_kernel_usb_faults` 的 argv/探针形状（#2957）是 host 侧事实，改动仍需真机验证，
  不在契约纯度判据覆盖范围内。
