# gpu_setup v1.2.3：adb/设备输出改宽容解码，修 v1.2.2 的整窗崩溃（#3069）

Status: implemented
Class: bug-fix

## Decision

**新增 v1.2.3（全量副本），把 `adb()` 的解码从严格改为宽容。** 不动 v1.2.2
（ADR-0020 不可变；原地改只会产生 conflict 且不更新基线）。

修的是什么：v1.2.2 新增的 `run_compat_probe()` 会把 `am instrument` 输出写到设备上、
再 `adb_shell("cat ...")` 读回来做签名分类，而这条路径经过：

```python
result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # _lib.py
```

`text=True` 按 locale **严格**解码，设备侧输出只要有一个非 UTF-8 字节就抛
`UnicodeDecodeError`。它**不是** `OSError`，`adb()` 里没有任何捕获，于是冒泡成
`gpu_setup` init 失败。生产实测（2026-09-22）：`0xf9` 落在 position 1，
run 496 的 456/476 台、run 500 的 462/487 台**全部**倒在这一步——
本该「早发现不兼容」的探测，变成了对所有设备的确定性崩溃。

改法（v1.2.3）：

- 新增 `decode_device_output(raw: bytes | None) -> str`：`decode("utf-8", errors="replace")`；
- `adb()` 改为收字节 + 该函数解码（stdout/stderr 都走），不再用 `text=True`——
  顺带摆脱 locale 依赖（agent 侧 `LANG` 未必是 UTF-8）；
- `gpu_setup.py` 的 `getprop sys.boot_completed` 直连 subprocess 同样改走该函数。

**为什么不只是给 probe 路径打补丁**：坏字节是设备输出的常态（instrument 日志、
UI dump、getprop 都可能带），任何一个 `adb` 调用点都可能踩到；在唯一的公共入口
收口，比在每个调用点加 try 更小也更稳。

**为什么 `errors="replace"` 不破坏判据**：`classify_compat_failure()` 是正则匹配
已知签名，坏字节只变成 U+FFFD，不影响匹配（用例
`test_signature_classification_survives_broken_bytes` 钉住这一点）。

## Alternatives

- **原地改 v1.2.2**：违反 ADR-0020；改完 `script.content_sha256` 与磁盘失配，
  引用它的 Plan 会在 precheck 被 `script_verify_failed` 阻断（2026-07-31 那次
  全平台派发中断就是这类漂移）。
- **给 `run_compat_probe` 单独 try 住 `UnicodeDecodeError`**：治标——同样的崩溃
  在 `dismiss_antutu_dialogs`（读 UI dump）与任何未来读设备输出的调用点都会复发；
  且「读不到日志」会被静默当成「没有签名」，反而掩盖真实故障。
- **把 probe 关掉（`compat_probe=false`）**：可作**临时止血**（回到 v1.2.1 行为），
  但 #774 要的「早发现不兼容」就没了。本 PR 让开关能在**开着**的情况下正常工作。
- **收窄成只对 probe 日志用 `errors="replace"`**：会在同一份 `_lib.py` 里留下
  两种解码纪律，下一个调用点靠猜——不值。

## Verification

| 检查 | 结果 |
|---|---|
| `pytest backend/agent/tests/test_gpu_setup_utf8_decode_3069.py -q` | **5 passed** |
| 变异①：`errors="replace"` 退回严格解码 | `test_tolerant_decode_*`、`test_signature_classification_*` **变红** |
| 变异②：`adb()` 改回 `text=True` | `test_v123_no_longer_decodes_strictly` **变红** |
| 还原后 | 5 passed —— 用例有判别力 |
| `pytest backend/agent/tests/ -q`（全量） | **2237 passed** |
| `python tools/dev/check-script-version-immutability.py --base origin/main` | **OK**（v1.2.2 未被原地改） |

用例形状：反例自证（同一段字节严格解码必抛）、宽容解码不抛且保留可读内容、
坏字节下签名分类仍命中、v1.2.3 源码不得再出现 `text=True` 代码用法（函数体内），
以及 v1.2.2 保持原样（证明修的是新版本而非原地改）。

## Revisit

- **注册与激活是独立的一步**：本 PR 只发版本目录。要生效还需要
  `POST /scripts/scan` 注册 + 引用该步骤的 Plan 重钉到 v1.2.3（模板族 pin 走 #2998 的机制）。
  在激活之前，GPU 窗仍会按 v1.2.2 全灭——**止血开关**是 Plan step params 或
  `STP_GPU_COMPAT_PROBE=false`（回到 v1.2.1 行为），标注为过渡。
- **同族排查**：其它脚本族（powercycle_setup / sleep / monkey 等）的 `_lib.py` 里
  也有 `text=True` 的 adb 封装，只是目前没有「读设备文件」的新路径踩到它。
  下次改到哪一族，顺手按本单口径收口；**不要**为此发起跨族批量改版（改动面大、
  收益未证）。
- **探测逻辑本身未动**：#774 的签名分类与「快速失败」语义保持原样；
  若后续发现 probe 在真实不兼容设备上仍不命中，那是 #774 的议题，不在本单。
