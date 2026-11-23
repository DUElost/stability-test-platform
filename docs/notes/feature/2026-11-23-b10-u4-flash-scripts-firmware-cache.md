# B10 U4：三大刷机族统一接入 Agent L1 固件缓存并退役 BSL 私有缓存

Status: implemented
Class: feature

关联：[#4086](https://github.com/DUElost/stability-test-platform/issues/4086)（批次方案 v1.6）、
[#4076](https://github.com/DUElost/stability-test-platform/issues/4076)、
[#4006](https://github.com/DUElost/stability-test-platform/issues/4006)、
[ADR-0064](../../adr/ADR-0064-host-firmware-cache-and-transfer-pacing.md) D4（Accepted v1.0）。

## Decision

按批次方案 [#4086](https://github.com/DUElost/stability-test-platform/issues/4086) §3 U4 与
[ADR-0064](../../adr/ADR-0064-host-firmware-cache-and-transfer-pacing.md) D1–D4 落地：

1. 三族 `capabilities.json` 声明 `requires_agent_capabilities: ["firmware_cache_v1"]`。
2. 四段执行次序：① 零设备访问启动自检 `FIRMWARE_CACHE_API_VERSION == 1`（失败 →
   `agent_firmware_cache_unavailable`）；② 保留现役只读选包 / `skip_if_current` /
   MTK 取锁前遗留门控恢复（不改 ADR-0063 D1–D7）；③ 主机锁外 `ensure_firmware`
   （`mtk` / `unisoc_fastboot` / `unisoc_bsl`）并持 `LOCK_SH`；④ 再取
   `/tmp/stp-flash-firmware.lock` 进入烧录。
3. `metrics.firmware_cache` 记录
   `{hit, elapsed_seconds, tier, sealed, cache_key, fallback_reason, permit_degraded}`；
   等待/拷贝打 `firmware-stage-wait` / `firmware-stage-copy`。
4. `flash_firmware_unisoc_bsl` 删除 `/var/tmp/stp_flash` 私有拷贝与主机侧裁剪；
   本地目录为 `fw_cache.local_dir/images`，super 固定
   `images/bsl/super_trimmed.img`；未封包在取锁前 `firmware_unsealed_not_supported`。
5. 新版本登记（方案目标号被中间迭代占用后顺延）：`flash_firmware@1.4.5`、
   `flash_firmware_unisoc@1.0.8`、`flash_firmware_unisoc_bsl@1.0.9`；模板 pin 同步；
   不 `--publish` / 不 scan；`pipeline_engine.py` 零改动。

## Alternatives

- 在 `pipeline_engine` 按 `script_name` 预拉固件：违背 #3779 与 ADR-0064 §3，否决。
- 保留 BSL 私有 `/var/tmp/stp_flash` 与 L1 并存：无法闭环 #4006 §8，否决。
- 版本号坚持方案字面 `1.4.4`/`1.0.7`：append-only 下中间登记已占用且 sha 漂移，
  只能顺延（见 PR「偏离说明」）。

## Verification

- `./scripts/project_python.sh tools/dev/check_governance_surface.py --base origin/main --check`
- `./scripts/project_python.sh tools/dev/check_script_packages.py`
- `./scripts/project_python.sh tools/dev/check_tool_manifest.py --base origin/main`
- `./scripts/project_python.sh scripts/run_gates.py check:quick`
- `./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/test_flash_firmware*.py backend/agent/tests/test_device_flash_scripts.py tests/test_inner_import_ratchet.py -q`

## Revisit

- 激活闸门（发包 / scan / Agent 滚动 / 计划重指 / `/var/tmp/stp_flash` 清扫）属
  `#4086` §5，本单元不得执行。
- 若现场发现固件包内硬编码绝对路径导致本地缓存目录无法直跑，按 §7 退回规划者。
