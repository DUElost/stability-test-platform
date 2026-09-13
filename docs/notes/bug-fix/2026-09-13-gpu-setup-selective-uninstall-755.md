# #755 gpu_setup 安装重试只卸当前失败包（v1.0.10）

Status: implemented
Class: bug-fix

## Decision

开 `gpu_setup` **v1.0.10**（ADR-0020/0029 不可变）：`_install_apk_stable` 在首次
`pm install` 失败后，按 APK 文件名映射只 `pm uninstall` 当前包，不再无条件卸
FULL+LITE。

v1.0.7 已收口 issue 的两处硬伤（双包名单次调用、push 全失败 NameError）；本版收口
建议中的「只卸当前失败包」，避免同批先装成功的包被重试清理误删。

Seed：`y0z1a2b3c4d5` 激活 1.0.10、停用 1.0.9。

## Alternatives

- **只关 issue 不改代码**：v1.0.7 已覆盖崩溃路径，但「误清同批包」仍在；issue 建议
  明确要求 selective uninstall，关单不完整。
- **原地改 v1.0.9**：违反脚本版本不可变。
- **重试前 `adb uninstall` 全部变体**：行为更粗，与 #755 建议相反。

## Verification

- `python -m pytest backend/agent/tests/test_gpu_power_sleep_resources.py -k v110 -q`
- `python tools/dev/check-script-version-immutability.py --base origin/main`
- `python scripts/run_gates.py check:quick`

## Revisit

若资源目录新增 Antutu/宿主 APK 文件名，需同步 `_retry_uninstall_pkgs_for_apk` 映射；
未知文件名当前不卸包（避免误伤）。
