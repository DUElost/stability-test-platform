# flash_firmware 超时逃生 SIGKILL 兜底 + poll 确认（#811）

Status: implemented
Class: bug-fix

## Decision

新增 `flash_firmware/v1.3.13`（v1.3.12 不可原地改，#816 已发布）：

超时逃生路径原为「SIGTERM 进程组 → `proc.wait(timeout=10)`」，若 flash_tool
卡不可中断 IO（如 NFS/CIFS 读大镜像）忽略 SIGTERM，`TimeoutExpired` 会被
抛出但**从不检查子进程是否真的死了**；调用方只记 attempt=timeout 就退避后
Popen 第二个实例 → 与未死实例并发抢刷同一 BROM/DA。

v1.3.13 改为：
1. SIGTERM 后 `proc.wait(timeout=3)` 宽限；
2. 仍在 → `killpg(SIGKILL)` 兜底 + `proc.wait(timeout=10)`；
3. **确认 `proc.poll() is not None`** 才允许抛出 `TimeoutExpired` 进入下一次
   尝试；若 SIGKILL 后仍存活 → `RuntimeError` fail-closed，绝不并发抢刷。

纯行为变更、`default_params`/`param_schema` 不变 → 无需 seed 迁移（与 #816
新增 v1.3.12 同口径，扫描自动建行）。影响面：新版本目录 + `tests/test_flash_firmware_timeout_811.py`。

## Alternatives

- 原地改 v1.3.10/v1.3.11——违反已发布版本不可变（AGENTS 硬不变量 + 扫描门禁），
  且会制造 content_sha256 conflict 使在途 Plan precheck 失败。
- 只加 SIGKILL 不校验 poll——无法证明进程已死，仍可能并发抢刷；poll 确认是
  修复的关键。
- 超时一律判失败——会改变既有 attempt 重试语义；本次只保证「终止确认」，
  重试决策留给调用方。

## Verification

- `tests/test_flash_firmware_timeout_811.py`：SIGTERM→（宽限）→TimeoutExpired
  且 poll 非 None；顽固进程 SIGKILL 后仍存活 → RuntimeError(fail-closed)；
  v1.3.13 存在且 v1.3.12 保留。
- `python tools/dev/check-script-version-immutability.py --base origin/main` →
  无已发布版本被原地改动。
- `ruff check` 通过。

## Revisit

- #810（mtbf run_dir 绑定）为同批延期项，按单独 PR 处理。
- 若 flash_tool 自身提供退出/日志契约，可进一步以工具级证据替代纯信号确认。
