# Agent Note: 展锐 BSL（spd_dump）静默直开刷机流水线

Status: implemented
Class: feature

## Decision

新增展锐 BSL/FDL2（`spd_dump`）整包刷机脚本族 `backend/agent/scripts/flash_firmware_unisoc_bsl/`（`1.0.0`）与 Pipeline 模板 `backend/schemas/pipeline_templates/flash_unisoc_bsl.json`：

1. **BSL 静默直开策略（方案 2）**：
   - 通过 `adb reboot autodloader` 进入 `1782:4d00` BSL 模式，加载 `fdl1-sign.bin`（`0x65008000`）与 `edk2-fdl2-sign.bin`（`0xBDFFFE00`）；
   - 擦除 `metadata`、`sysdumpdb`、`misc`、`sd_klog`、`uboot_log` 并写入 4KB `userdata.img`，配合全量 A 槽及公共分区刷写，实现单次冷启动直进系统（绕过 Recovery 与 Sysdump 白屏）；
   - 严格拦截受保护 NV/校准分区（`prodnv`、`calinv`、`miscdata`、`persist`、`splloader`、`nr_*nv*`）。
2. **绕过 ~300s BSL 看门狗**：
   - 将固件镜像缓存到本地 SSD（`/var/tmp/stp_flash/fw_cache`）避免 NFS 读延迟；
   - 解析 `super.img` sparse header 动态裁剪尾部 `CHUNK_DONT_CARE`（16 MB 对齐，P532AA 上由 6000 MB 裁至 2464 MB），实测单台 `spd_dump` 耗时稳定在 ~178–238s。
3. **与既有刷机流解耦与锁协同**：
   - 独立脚本族与独立 Pipeline 模板，不修改既有 `flash_firmware` / `flash_firmware_unisoc`；
   - 共用主机级排他锁 `/tmp/stp-flash-firmware.lock`（`flock`，每 5s 输出 `stage="lock-wait"` `PROGRESS` 戳，且在 `protected_regular=2` 下对 `O_CREAT` 做 `PermissionError` 回退）。

## Alternatives

- **直接复用 `flash_firmware_unisoc`（fastboot）**：P532AA 从 `user` 跨刷 `userdebug` 无法走 fastboot 解锁刷写路径；而将 BSL 逻辑塞进 `flash_firmware_unisoc` 会耦合两套完全不同的传输通道与超时模型，因此拆分为独立脚本族 `flash_firmware_unisoc_bsl`。
- **仅使用 Ansible 外部批量脚本**：无法纳入 STP 控制面 `PlanRun` 状态机、进度观测、自动 `oobe_skip` 与后续稳定性压测编排。

## Verification

- 单元测试：`.venv/bin/python -m pytest backend/agent/tests/test_flash_firmware_unisoc_bsl.py -v`（5 passed）。
- 包与清单门禁：`.venv/bin/python tools/dev/check_script_packages.py` 与 `.venv/bin/python tools/dev/check_tool_manifest.py --base origin/main` 通过。
- 实机批量验证：`172.21.15.72` 16/16 台 P532AA 方案 2 批量刷机全数成功升级至 `userdebug` inc=117（总耗时 ~66 分钟）。
- STP 契约双机并发验证：`172.21.15.82` 上以 `android` 用户并发运行 `MSW000003J6AU0001C`（`flash=238.3s`, `boot=63.0s`, `total=304.8s`）与 `MSW000003J6AU0006A`（`lock-wait` 心跳正常，`flash=233.7s`, `boot=60.9s`, `total=539.0s`），均返回 `success=true` 且 `uid=0(root)`。

## Revisit

- 若后续同一主机需高频混刷多套不同版本固件，可将 `cache_dir` 按固件路径 hash 分隔子目录。
- `flash_firmware_unisoc_bsl` 上线需完成：PR 合入 `main` -> `--publish --packages-root /mnt/stp-aee/packages` -> `POST /scripts/scan` -> 控制面发布同步模板（`flash_unisoc_bsl.json`）。
