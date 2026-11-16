# B9-U2：flash_firmware_unisoc_bsl@1.0.3 安全闸门收口

Status: implemented
Class: bug-fix

## Decision

依据批次 B9 规划方案（#4006 §3.1、§3.2 J1–J13、§3.3）对 `flash_firmware_unisoc_bsl` 实施安全边界收口并登记为 `1.0.3`，同时将模板 `backend/schemas/pipeline_templates/flash_unisoc_bsl.json` 的 pin 追平至 `1.0.3`（#4008）：

- **固件参数唯源（J1）**：移除所有 P532AA 机型常量（FDL 文件/地址与分区表常量）及 `userdata.img` 回落；`images/plan.json` 必须存在且包含 `entries`（非空）、`fdl.fdl1.{file,base}`、`fdl.fdl2.{file,base}`、`erases`、`UserData` 条目及非空可写分区，否则在启动 `spd_dump` 或 `adb reboot autodloader` 前拒绝。
- **身份绑定与指针不回退（J2/J3/J11）**：三条路由（显式 `firmware_dir`、`family`+`version`、`family`+`latest.json`）均实读 `ro.product.model`、`ro.product.device`、`ro.product.name` 并读取 `manifest.json`（校验 `platform == "unisoc"`、非空 `models`、`-`/`_` 归一化交集非空、非空 `version`/`version_prop`）；`latest.json` 存在 `versions` 映射但无命中时不回退族级 `version`；成功与拒绝结果的 `metrics.route` 均记录 `decided_by`、三个实读身份值、`family`、`version` 与 `manifest` 路径。
- **擦除白名单与槽位受保护分区（J4/J5/J6）**：实际擦除集取 `plan.erases` 与 `{metadata, misc, sysdumpdb, uboot_log, sd_klog}` 的交集，被跳过项在 `metrics.skipped_erases` 列出；受保护分区判定前剥离 `_a`/`_b` 后缀并将 `spl`、`splloader` 及其带槽位形式纳入拦截，保持 `preserve` 跳过。
- **前置闸门与子进程有界（J7/J8）**：增加电量校验（`battery_min_level=5`、`skip_battery=false`）与启动前 USB 总线 `1782:4d00` 检查；`adb reboot autodloader` 失败或超过 `spd_dump_timeout_seconds`（缺省 `3600s`，不超过模板 `7200s` 步骤超时）时终止并回收 `spd_dump` 子进程，返回前不留孤儿/僵尸进程。
- **成功判据与缓存校验（J9/J10）**：成功必须同时满足 `spd_dump` 返回 `0`、正向前后重启证据（`boot_id` 变化、`debug.stp_bsl_nonce` 清除或 `uptime` 回落）以及无关闭开关的 `manifest.version_prop` 回读等于 `manifest.version`；刷写参数直接取自本次源目录 `plan.json`，缓存文件与裁剪 `super` 均做 SHA-256 内容比对，缓存准备失败时清理本次 `.tmp.*` 暂存文件，且缓存准备保持在主机锁内。
- **保持不变（J13）**：主机锁路径与语义、`PROGRESS` 戳、CLI 参数与 `spd_dump` 参数形状不变。

涉及文件：`backend/agent/scripts/flash_firmware_unisoc_bsl/flash_firmware_unisoc_bsl.py`、`backend/agent/tests/test_flash_firmware_unisoc_bsl.py`、`backend/schemas/pipeline_templates/flash_unisoc_bsl.json`、`tool_manifest.json`、本文档。

## Alternatives

- **继续在 `plan.json` 缺键时回落 P532AA 默认常量**：已否决，缺键固件包会静默套用错误 FDL 加载地址与分区表，直接违反 #4006 F1/J1。
- **仅靠文件长度与 mtime 判定缓存一致**：已否决，等长替换镜像或篡改 `plan.json` 时仍会命中旧缓存（#4006 F5/J10），改用 SHA-256 校验与源目录直接读 `plan.json`。
- **在 B9-U2 内一并迁移 `spd_dump` 到 `requires_tools`**：已否决，按 #4006 §3 归属 U4（#4010）在 U2 合入且工具包登记完成后实施，避免与 U2 安全闸门耦合。

## Verification

- `./scripts/project_python.sh tools/dev/check_script_packages.py`
- `./scripts/project_python.sh tools/dev/check_tool_manifest.py --base origin/main`
- `./scripts/project_python.sh scripts/run_gates.py check:quick`
- `env -i PATH="$PATH" PYTHONPATH=. .venv/bin/python scripts/run_pytest.py backend/agent/tests/ -q`
- `./scripts/project_python.sh scripts/run_pytest.py tests/ -q --ignore=tests/test_alembic_upgrade.py --ignore=tests/test_script_seed_governance.py --ignore=tests/test_diag_readonly_role_pg.py --ignore=tests/test_seed_default_params_face_3289.py --ignore=tests/test_disarm_guard_migration_3752.py`
- 变异自证（#4006 §4 第 22 条）：分别临时禁用第 5、8、14 条对应检查并运行对应单测确认变红，随后恢复。

## Revisit

- #4006 §2 的三条模型级问题仍未解决，后续需按对应载体 issue 跟进：
  1. **同 model 不同 product 的身份歧义**（#3762）：A57 与 A57Pro 的 `ro.product.model` 与 `ro.product.device` 相同、仅 `ro.product.name` 不同；A57 系列接入 BSL 前必须先完成 #3762 裁决。
  2. **BSL 口级绑定**（#3995 §5 第 3 项）：`spd_dump` 不支持按串号或 USB 端口选设备，J7 仅在启动前检查总线，无法排除启动后并发设备进入下载模式。
  3. **三份主机锁实现与跨族共享 helper**（#3498）：本批未合并三份主机锁实现。
- 激活与真机核对（`manifest.version` 与 `version_prop` 实机回读值、双机 `dry_run` 与 L2 金丝雀）在 #4006 §5.3 激活 A 中由 Owner 执行；未完成前不得解除 BSL 冻结。
