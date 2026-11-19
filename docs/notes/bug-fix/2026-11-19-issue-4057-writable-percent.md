# 磁盘百分比判据改用可写占比，消除保留块天花板（#4057）

Status: implemented
Class: bug-fix

## Decision

1. `backend/agent/system_monitor.py::get_disk_usage` 增报
   `writable_percent = used/(used+avail)`（等价 `df` 的 `Use%`）与 `avail_gb`
   （与 `free_gb` 同值，存在的理由是给消费面一个不会被误读成「含保留块」的名字）。
   两个旧字段 `usage_percent` / `free_gb` 保持不变，不破坏既有读法。
2. `capacity_reporter._disk_usage_percent` 改为优先取 `writable_percent`，缺失时回退
   `usage_percent`。因为该函数同时被 `_compute_health`（`disk_high`）和
   `_compute_health_limit`（打闸返回 0）使用，一处改动即覆盖两个共享阈值的判据，
   不新增 reason 词汇、不动前端与指标标签。
3. `local_disk_monitor._current_usage_pct` 同样优先 `writable_percent`、缺失回退。
   日志键名 `hdd_usage_percent_missing/invalid/out_of_range` 全部保留，
   只在回退时加一条 debug；避免改动可能被日志告警匹配的字符串。
4. 更正 `get_aee_disk_usage` 的注释：旧注释称「阈值口径与 HddSpill（95%）对齐」，
   而实际默认是 `stp_hdd_spill_critical_pct = 98.0`（`backend/agent/settings.py:126`），
   两个数在带保留块的盘上都不可达。
5. `docs/development/environment-variables.md` 的 spill 阈值行写明「一律按可写占比比较」。

## Alternatives

- 新增 `disk_avail_exhausted` 之类的 reason 让写满直接可见：本 PR 不做。
  它会改 health reason 词汇，必须同步 `api/routes/metrics.py` 的 `_HEALTH_REASONS`
  （Prometheus label 分桶）、前端 `ExpandableHostTable` 的 `REASON_LABELS`、告警选择器与文档，
  属跨面变更（且更接近方向级决策）。作为 #4057 第二阶段单独推进。
- 直接改阈值数值（如把 98.0 降到 90.0）让它在旧口径下可达：放弃。那是在错误的坐标系里
  调刻度，既改变运维已知的语义，也仍然随各 host 的保留块比例漂移。
- 只加 `avail_gb` 字段、不动判据：放弃。那样「看得见但仍不报警」，`.62` 这类主机仍会
  在 `avail=0` 时保持 HEALTHY。
- 在控制面侧换算（Agent 不改）：放弃。Agent 才是 `shutil.disk_usage` 的唯一调用点，
  控制面只拿到已经丢掉了保留块信息的比值；且 fleet 上 Agent 逐台下发比控制面单点改动更慢，
  越晚改，历史数据里「看起来健康但其实写满」的样本越多。

## Verification

- 单元：`backend/agent/tests/` 下 `test_system_monitor.py`（保留块场景与三个断言字典同步更新）、
  `test_capacity_reporter.py`（可达性回归 + 优先级/回退三条）、
  `test_local_disk_monitor.py`（`writable` 优先、回退、两键皆缺返回 None）。
  `run_pytest.py backend/agent/tests/`：**3508 passed, 1 failed, 2 skipped**。
  唯一失败 `test_flash_preflight_v102.py::test_dialout_absent_and_no_rule_fails`
  在**未打补丁的 main 检出上同样失败**（0.06s 同点断言），与本改动无关，已按现状记录。
- 静态：`ruff check` 与 `compileall` 对改动的 3 个模块 + 3 个测试文件通过；
  `check-internal-ip-leak.py`、`check_governance_surface.py --check` 结果见 PR。
- 语义依据（本机实测，非推断）：`shutil.disk_usage` 的 `used == (f_blocks - f_bfree) * frsize`
  （不含保留块）、`free == f_bavail * frsize`；本机 `/` 保留块 22.63 GiB / 444.1 GiB
  ⇒ 天花板 **94.91%**，与现网 `.75`、`.62` 上报值完全一致；`/mnt/stp-aee` 保留块仅 0.11 GiB
  ⇒ 天花板 ≈99.99%。**同一份代码在不同保留块比例下给出不同可比性**，这正是旧判据不可靠的根因。
- 真实输入回放：`.62` 上报 `used_gb≈869.22 / free_gb=0.0` ⇒ 旧判据 94.91（不报警、
  spill 也不紧急），新判据 100.0（`disk_high` 可达、critical 98.0 可达）。

## Revisit

- **#4057 尚未完全修复**：本 PR 让判据可达、让事实可见，但 `.62` 满的是 **AEE 盘**，
  而 health 判据至今只读系统盘 `system_stats["disk_usage"]`，`disk_usage_aee` 从不参与
  reason 计算 ⇒ `.62` 升级后仍会是 HEALTHY。第二阶段需把 AEE 盘纳入 health（含
  reason 词汇的四处同步，见 Alternatives）。
- 即便阈值可达，spill 在 `.62` 上仍救不了它：该机 `state=LOCAL` 事件为 0，
  占用者是 322 个 `.stp-scan` staging 目录（非 DLE 对象）⇒ 需要一次性回收，属运维动作。
- 若第二阶段确定要做 reason 词汇，先核对 `docs/design/` 是否已有覆盖该面的契约；
  若属于方向级决策，按 AGENTS.md 走 ADR 而非只写本 Note。
- 若发现某 host 的保留块被现场调大（`tune2fs -m`），旧口径的天花板会进一步下降，
  本改动使该差异不再影响判据；届时无需再调整阈值。
