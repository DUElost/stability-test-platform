# monkey_setup v2.3.7：push 解包退出码检查 + marker 后置条件（#1027）

Status: implemented
Class: bug-fix

## Decision

#1027（R08-F07）：`step_push` 的 bundle 分支调用 `adb_shell_progress`（tar 解包
复合命令）后**不检查返回的 CompletedProcess 退出码**，直接
`return {"success": True}`——空间不足 / 权限 / 损坏归档导致解包失败时，准备阶段
带病继续（#812 同族：`push_resources` / `fill_storage` / `clean_env` 的 adb 检查
缺失，另单）。

修复（新脚本版本 **v2.3.7**，从 v2.3.6 fork——v2.3.6 已被 #894 的 att_clean
占用，issue 证据里的 v2.3.5 已不是最新发布版）：

- 检查 `proc.returncode`：非零 → `{"success": False, "error": "tar extract
  failed rc=N: <stderr/stdout 摘要 300 字>"}`；
- `subprocess.TimeoutExpired` 显式转失败（此前会外溢——但复合命令可能半途
  而废，结果同样不可信）；
- **后置条件**：`cat .stp_bundle_sha256` 内容 == manifest 期望值。复合命令
  `tar xf && rm && echo sha > marker` 的 tar 段成功但 echo 段失败时 rc 也可能
  为 0，只有 marker 可信；marker 缺失/不符 → 失败；
- 退出码语义按 ADR-0033 §D2 方向约定取工具自有域：脚本整体退出码不变
  （成功 0 / 失败 1），不引入 3–123 / ≥124 值；
- `#139` 的 tar PROGRESS 心跳戳行为不变（`tar_start` / 周期戳 / `tar_end`），
  失败路径也打 `tar_end`。

与 ADR-0033 的关系：无直接约束。本单在现行 ADR-0020 版本域内；§D2 只作为
退出码取值的方向性对齐（见上），不需要 ADR 落地。

## Alternatives

- 只查退出码不加 marker 后置条件：复合命令 `tar && rm && echo` 的 rc 覆盖全链，
  正常情况够用；但「tar 成功、echo 被打断」的窗口产出的是无 marker 的半成品，
  下轮 `skip_if_match` 比对会 miss 而重推——加后置条件把这类失败也拦在准备阶段；
- 在 shell 侧用 `set -e`：等价于 rc 检查，但错误细节（stderr 摘要）不如 Python
  侧收集清晰；
- 同批修 #812 的 push_resources/fill_storage/clean_env：同族不同单，各自动
  版本发布，避免一个 PR 扫四个脚本版本目录。

## Verification

- `pytest backend/agent/tests/test_monkey_setup_v237.py`：6 passed——rc=0 且
  marker 匹配成功 / rc=1 空间不足报失败（error 含 rc 与 stderr）/ stderr 空时
  回落 stdout / TimeoutExpired 转失败 / marker 不符判失败 / 继承 v2.3.6 的
  att_clean 默认步骤；
- **缺陷复现对照**：同一场景（tar rc=1 "No space left on device"）在 v2.3.6
  实测返回 `{'success': True, 'bundle': 'n', 'files': 1}`，v2.3.7 返回失败；
- `check-script-version-immutability.py --base origin/main`：OK（v2.3.6 原地
  未动）；ruff 干净。

## Revisit

- #812（push_resources / fill_storage / clean_env 的 adb 退出码检查）是同族
  余下部分，待独立认领；
- marker 后置条件依赖 `cat` 一次额外 adb 往返（~百 ms 级），相对 600s 解包
  上限可忽略；
- `skip_if_match` 前置比对在 marker 缺失时静默 try/except——与既有语义一致，
  未改动。
