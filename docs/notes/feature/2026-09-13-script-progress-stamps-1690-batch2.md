# 长耗时脚本补 PROGRESS 打戳：第二批五脚本（#1690）

Status: implemented
Class: feature

## Decision

#1690 第二批（接 PR #1726 首批三脚本）：把「长耗时步骤必须打戳」的 #115
契约推进到台账中的五个长耗时路径脚本，各自 fork 新版本（ADR-0020，旧版本
原地不动）：

| 脚本 | 新版本 | 打戳覆盖 |
|---|---|---|
| push_resources | v1.0.1 → **v1.1.0** | 逐文件 push（`push:<name>`）、bundle push（`bundle_push:<name>`）、tar 解包（`bundle_unpack:<name>`，timeout=600） |
| monkey_resource_push | v1.0.1 → **v1.1.0** | 统一 `_push()` 入口（含 timeout=300 的媒体资源与 arch 目录） |
| install_apk | v1.0.2 → **v1.1.0** | `adb install` 段（`apk_install:<name>`） |
| monkey_launch | v5.0.1 → **v5.1.0** | 两个启动后轮询（`watchdog_wait` / `aimwd_wait`）逐次 `progress_tick` |
| clean_env | v1.0.1 → **v1.1.0** | `pm uninstall`（`uninstall:<pkg>`）与日志目录清理（`clear_logs:<dir>`） |

实现与首批同构：每个新版本自带 `_adb.py` 拷贝，内含 `progress_stamp` /
`progress_heartbeat`（start + 周期心跳 20s + end，seq 进程内单调）/
`progress_tick`；`capabilities.json` 五处声明 `["progress_stamps"]`。

同时收口两件事：

1. `test_script_catalog_capabilities.py` 的 `_PROGRESS_THRESHOLDS` 补齐 8 个
   打戳族（首批 3 + 本批 5）——新版本漏放 `capabilities.json` 时该测试直接
   红掉（此前只覆盖 monkey_setup / flash_firmware，收敛后才有此守卫）；
2. 修正台账判读：上一轮扫描用 `progress_stamp|heartbeat|tick` 三个符号名
   grep，把用自研 `_emit_progress` 的 `aee_prepare/v1.0.1` 误记为缺戳（其
   实际输出 `PROGRESS {…}` 协议）。按协议字符串重扫后，真实缺戳 = 9 个短
   操作脚本（aee_signal_trigger / check_device / connect_wifi / ensure_root /
   monkey_check / monkey_teardown / monkey_test / noop / stop_aimonkey），
   本批 5 个已清。

## Alternatives

- clean_env 不打戳（秒级操作）：评估后仍补——`pm uninstall` 与 `rm -rf` 大
  日志目录单项 30s 上限、多项串联可超百秒；打戳无副作用（stderr 附加行被
  reader B 丢弃），且让停滞钟对该步骤可用；
- 在 `_adb.py` 里给 `adb_push` 统一包心跳（一处接线覆盖全部调用方）：会让
  所有短 push 也产生 start/end 戳，噪声大且阶段语义丢失；选择在调用点按
  语义命名；
- 真实字节进度（解析 adb push 输出）：真机实测非 TTY 下 adb push 不输出进度
  （#115 背景），固定心跳已满足停滞钟活性。

## Verification

- `pytest backend/agent/tests/test_script_progress_stamps_1690_batch2.py`：
  24 passed——五个库的 heartbeat/tick、五类接线（逐文件/bundle、`_push`、
  install、两个轮询、uninstall/clear_logs）、真实链路 stderr 上的
  `PROGRESS` JSON 与 seq 单调、五处 capabilities 声明；
- `pytest backend/agent/tests`（全量，`JWT_SECRET_KEY=ci-test-secret-key`）：
  1847 passed；
- `pytest backend/tests/services/test_script_catalog_capabilities.py`：8 passed
  （补齐阈值后 8 个打戳族的声明/DB 一致性全绿）；
- `python tools/dev/check-script-version-immutability.py --base origin/main`：OK
  （旧版本原地未动）；
- `ruff check backend/ tools/ scripts/`（CI 同款）：All checks passed；
- `python tools/dev/check_governance_surface.py --check`：S1–S13 全绿；
- `python scripts/run_gates.py check:quick`：7 gates 全绿。

## Revisit

- 剩余 9 个短操作脚本逐批收敛；若其中任一操作在慢设备上变长，再评估打戳；
- `progress_heartbeat` 已重复第 8 份拷贝（ADR-0020 每版本自带依赖，不共享）
  ——如后续再批量补戳，考虑生成式拷贝或在脚本骨架中固化；
- 心跳间隔 20s 与 `stall_seconds` 建议值（≥120s）的关系：若未来默认停滞钟
  收紧到 <20s，需同步调小间隔。
