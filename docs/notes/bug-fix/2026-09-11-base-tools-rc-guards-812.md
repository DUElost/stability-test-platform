# 基础小工具 rc 守门（#812）——clean_env / push_resources / fill_storage v1.0.1

Status: implemented
Class: bug-fix

## Decision

本质问题（#812，四域审查 B×3）：三个基础小工具吞 rc 误报成功——

1. `clean_env/v1.0.0`：`adb_shell` 只返回 stdout，`pm uninstall` 失败输出
   "Failure [...]" 因不含 "not installed" 被计入成功；`rm -rf` EACCES（rc=1）
   仍计 `logs_cleared`；`setprop` 同样不查 rc；
2. `push_resources/v1.0.0`：bundle 解包链（`cd && tar xf && rm && echo sha >
   marker`）整串不查 rc——半途失败仍 `success=True` 带 file_count；
3. `fill_storage/v1.0.0`：`dd` rc 不查、不复查 df——ext4 保留块/并发写入让 dd
   提前 ENOSPC 时仍报成功且虚报 `filled_kb=need_kb`。

修复 = **发新版本 v1.0.1 × 3**（版本目录不可变，新行为走新版本）：

- `clean_env/v1.0.1`：全链路改 `adb_shell_quiet` 查 rc；uninstall 仅 rc=0 且
  stdout 含 "Success" 时计数（"not installed" 视为本就未装跳过）；
  rm/mkdir/setprop rc 非零计错；
- `push_resources/v1.0.1`：解包链 rc 判定 + 解包后回读 `.stp_bundle_sha256`
  核验与期望 sha 一致才报成功；
- `fill_storage/v1.0.1`：dd rc 判定 + 完成后回读 df 核验
  `actual_pct ≥ target_pct` 才报成功，成功 metrics 携带 `actual_pct`。

## Alternatives

- **原地修改 v1.0.0**——禁止：已发布版本不可原地修改（AGENTS.md 硬不变量）；
- **clean_env 仅查 rc 不看 "Success" 关键字**——放弃：uninstall 成败语义在
  stdout（"Success" / "Failure [...]"），双重判定（rc + 关键字）最稳；
- **fill_storage 只查 dd rc**——放弃：dd 正常退出也可能未写满（并发写入争用），
  回读 df 是"达到 target"的唯一可信判据（issue 明确要求）；
- **push_resources 只查 rc 不验 marker**——放弃：rc 已覆盖解包失败，但 marker
  是"下次 skip 判定"的权威，回读核验防止部分解包后的自愈错位（issue 要求）。

## Verification

实际运行（worktree `/tmp/stp-812`，基于 `origin/main`）：

- `pytest backend/agent/tests/test_base_tools_rc_guards.py -v` → **10 passed**
  （uninstall 失败/未装/Success、clear_logs rc、dd rc、df 回读不足/达标、
  解包 rc、marker 不符/一致）；
- **反向验证**：测试指向 v1.0.0 → **10 failed**（接口与行为均不兼容，任何回退
  都会被抓住）；恢复后 10 passed；另做同接口精细 mutation（clean_env 判定改回
  假成功）→ 1 failed，恢复后全绿；
- `pytest tests/test_script_version_immutability_gate.py` → 通过（新增版本目录
  合规）；
- `check:quick` → 7 gates 全绿。

未完成（pending）：

- 真机侧：三脚本在设备上按新版本执行（含失败注入：无 root 清场 / 磁盘写满 /
  损坏 bundle）——需真机或隔离环境验证。

## Revisit

- 若 `pm uninstall` 输出格式随 Android 版本变化（"Success"/"Failure" 之外），
  扩展关键字判定；
- fill_storage 的 df 回读增加一次设备往返——若成为热路径瓶颈，可改为
  `stat fill.bin` 估算 + df 抽查。
