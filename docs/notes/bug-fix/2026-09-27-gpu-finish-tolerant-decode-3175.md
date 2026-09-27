# gpu_finish v1.0.8：adb 收字节 + 宽容解码，坏字节不再炸在 pkill 之前（#3175 / B1-G2）

Status: implemented
Class: bug-fix

关联：[#3175](https://github.com/DUElost/stability-test-platform/issues/3175)（本单）、
[#3463](https://github.com/DUElost/stability-test-platform/issues/3463)（批次 B1 规划方案 §3 G2 / §4 G2）、
#3069（gpu_setup 侧同形态生产回归：462/487 台倒在 `0xf9`）、gpu_setup v1.2.3（参照实现）。

## Decision

按 #3463 §3 G2 执行，不重新设计：

1. 把 gpu_setup v1.2.3 的 `decode_device_output()`（bytes + `errors="replace"`）同构 port 进
   `backend/agent/scripts/gpu_finish/_lib.py`，`adb()` 改「显式收字节再解宽容码」，去掉 `text=True`；
2. 登记新版本 **gpu_finish 1.0.8**（源树改动用 `--register`，manifest append-only）；
3. 把 `test_gpu_setup_utf8_decode_3069.py` 的 SourceGuard 判据扩展到 gpu_finish 目录，并补
   `stop_stress()` 的坏字节反例。

原因（#3175 证据）：finish 侧只修了一半——`gpu_finish.py` 的日志读取走 `errors="replace"`，但
`_lib.py` 的 `adb()` 仍是 `text=True` 严格解码。`stop_stress()` 的 force-stop 回显里一个非 UTF-8
字节就抛 `UnicodeDecodeError`（非 `OSError`，调用点无从兜住），teardown 中断在 `pkill` 之前，
**压测循环留在设备上继续跑**，污染后续窗口。

## Alternatives

- **只改 `gpu_finish.py` 调用点、不动 helper**：崩溃点在 helper 内部的 `subprocess.run(text=True)`，
  调用点包不住；且 §6 明确不做「改跨族 `adb_shell` 签名」。
- **在 `adb()` 里 `try/except UnicodeDecodeError` 兜底**：与参照实现（bytes + `decode_device_output`）
  形成两套形态，SourceGuard 无法机械判「严格解码入口」，违背「逐族同构 port」。
- **顺带修其余约 20 族的 F2 严格解码**：#3463 §6 明确不做（登记为证据）。

## Verification

- `python -m pytest backend/agent/tests/test_gpu_setup_utf8_decode_3069.py -q` → **7 passed**
  （新增 2 条：gpu_finish SourceGuard 判红 `text=True, timeout=` 形态；坏字节下 `stop_stress()`
  执行到 `pkill`）。
- **反例（变异验证）**：把 `_lib.py` 的 `adb()` 临时回退为 `text=True` 原形并清 `__pycache__`
  后复跑 → 两条新用例失败（`FormRegression`：形态守卫；`UnicodeDecodeError`：炸在 pkill 之前），
  `#3069` 既有 5 条仍绿；已恢复修复并复跑 7 passed。
- `python tools/dev/check_script_packages.py` → 绿（35 个族树与最新登记等价，gpu_finish@1.0.8
  sha=`17fdfde215cb`）。
- `python tools/dev/check_tool_manifest.py --base origin/main` → 绿（40 族 / 221 条目，append-only）。
- `python -m pytest backend/agent/tests/ -q -k "gpu"` → 76 passed, 2132 deselected。
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (16 gates)`。

## Revisit

- **gpu_check 族未覆盖**：同目录 `gpu_check/_lib.py` 仍是自有副本（#3463 §1 形态扫描：本批只修
  「已要升版本的族」）；helper 副本模型本身是模型级问题（#3463 §2，A/B/C 待 Owner 裁决）。
  若维持现状（C），下次改 gpu_check 时同构 port。
- **生效未做**：按 #3463 §5 由 Owner 窗口统一 publish + scan + 重指；本单合入不等于设备端已用上
  1.0.8。真机侧无设备可验（本机无 agent 连接），验证到静态守卫 + 打桩反例为止。
