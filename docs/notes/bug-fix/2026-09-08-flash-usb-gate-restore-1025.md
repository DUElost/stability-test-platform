# 刷机门控口在取消/超时后不再滞留 authorized=0（脚本 v1.3.11）

Status: implemented
Class: bug-fix

## Decision

#1025（R08-F02）：`flash_firmware` 的门控恢复只挂在 `_settle_lock()`（正常完成 /
异常 / 失败三处），脚本全程没有信号处理器。Agent 取消与超时是向进程组发
SIGTERM→SIGKILL，进程直接死在结算之外 → 被门控的邻机 MTK 口永久滞留
`authorized=0`（authorized 只对当前 USB 实例生效，重枚举也不会自己回来），邻机
在人工干预前一直不可用。

修复（新脚本版本 **v1.3.11**，v1.3.10 原地不动 —— ADR-0020 已发布版本不可变）：

- 新增 `_install_termination_cleanup(settle)`：注册 SIGTERM/SIGINT/SIGHUP；
- 处理器先 `settle(signum)`（main 里即 `_settle_lock()` + `signal-cleanup` 进度
  戳：恢复门控 + 释放 host lock），再把该信号还原为 `SIG_DFL` 并 `os.kill(self)`
  —— **仍以 WIFSIGNALED 收场**，退出码命名空间不变（ADR-0033 §D2 把信号死亡归为
  工具缺陷，本单不改这层语义）；
- self-signal 异常时 `os._exit(128 + signum)` 兜底，绝不能吞掉取消；
- 注册点放在 gating 与 host lock 就位之后（`_settle_lock` 定义之后）：注册窗口
  内被 SIGTERM 命中时 flock 由内核在进程死亡时释放，无额外泄漏面；
- 非主线程注册 / Windows 无 SIGHUP → 捕获异常并保持原行为，不影响刷机主流程。

**与 ADR-0033 的关系**：无直接约束。ADR-0033 管工具打包与接入契约（Tool Contract、
Package Store、三层宿主分类）与退出码命名空间，全文无门控/清理条款；本单只借道
其 §D2 的「信号死亡语义」，未改判据。真正约束本单的是 ADR-0020（必须发新版本）。

## Alternatives

- **Agent 持门控记录 + 杀树后恢复**（issue 原建议）：能覆盖 SIGKILL 与崩溃，但要
  新增记录落点（env/状态文件路径）、Agent 侧恢复逻辑与联调测试，跨脚本 + Agent；
  本轮按用户裁决选最小改动，见 Revisit。
- 只加 `finally` / `atexit`：都不覆盖信号终止与 SIGKILL（`atexit` 不跑）。
- 处理器里 `sys.exit(...)` 正常退出：会把信号死亡改成正常退出码，侵占 ADR-0033
  的退出码命名空间，放弃。

## Verification

- `pytest backend/agent/tests/test_flash_firmware_v1311.py`：4 passed —— 先结算再
  self-signal、结算抛错仍继续退出、三个信号均注册、main 集成确认挂上了处理器；
- **真实进程 SIGTERM 实测**（临时脚本，未入库）：构造假 sysfs 两 MTK 口 →
  `_gate_other_mtk` 把非目标口写 `authorized=0` → 装处理器 → 发 SIGTERM：
  `authorized` 回到 `1`，进程 `returncode == -15`（信号死亡，语义未变）；
- `tools/dev/check-script-version-immutability.py --base origin/main`：OK（无已发布
  版本被原地改动）；
- `pytest backend/agent/tests`：全目录通过；ruff（`backend/agent/scripts` 在
  ruff.toml 的 extend-exclude 内，CI 不覆盖）。

## Revisit

- SIGKILL-only（脚本被外部 kill -9、崩溃、host 掉电）仍不覆盖 —— 那时需要
  Agent 托管的门控记录（issue 原建议）或开机/认领时的 sysfs 对账清扫；
- 门控恢复依赖写 sysfs 权限（root / dialout+udev / passwordless sudo），恢复失败
  目前只落 `gating.restore.errors`；若生产出现恢复失败，下一步应把它升级为告警
  而不是静默丢弃；
- 若将来 ADR-0033 落地 Tool Contract 的清理钩子，本处理器可退化为契约内的
  `on_cancel` 实现，行为不变。
