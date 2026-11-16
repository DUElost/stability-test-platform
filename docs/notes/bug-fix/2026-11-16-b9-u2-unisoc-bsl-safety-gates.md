# B9-U2：flash_firmware_unisoc_bsl@1.0.3 安全闸门收口

Status: implemented
Class: bug-fix

## Decision

依据批次 B9 规划方案（#4006 v1.1 §3.1、§3.2 J1–J13、§3.3）对 `flash_firmware_unisoc_bsl` 实施安全边界收口并登记为 `1.0.3`，同时将模板 `backend/schemas/pipeline_templates/flash_unisoc_bsl.json` 的 pin 追平至 `1.0.3`（#4008）：

- **固件参数唯源（J1）**：移除所有 P532AA 机型常量（FDL 文件/地址与分区表常量）及 `userdata.img` 回落；`images/plan.json` 必须存在且包含 `entries`（非空）、`fdl.fdl1.{file,base}`、`fdl.fdl2.{file,base}`、`erases`、恰有一条 `UserData` 条目（`id="UserData"` 且 `partitions` 含 `userdata`，其它条目 `partitions` 含 `userdata` 时拒绝）及非空可写分区，否则在启动 `spd_dump` 或 `adb reboot autodloader` 前拒绝；`super` 镜像路径从 plan 条目 `file` 与 `raw` 读取并在 `dry_run` 与缓存准备中保持一致。
- **身份绑定与指针不回退（J2/J3/J11）**：三条路由（显式 `firmware_dir`、`family`+`version`、`family`+`latest.json`）均实读 `ro.product.model`、`ro.product.device`、`ro.product.name` 并读取 `manifest.json`（仅读取 §3.1 允许的 `platform`、`models`、`version`、`version_prop` 四个键，校验 `platform == "unisoc"`、非空 `models`、`-`/`_` 归一化交集非空、非空 `version`/`version_prop`）；`latest.json` 存在 `versions` 映射但无命中时不回退族级 `version`；成功与拒绝结果的 `metrics.route` 均记录 `decided_by`、三个实读身份值、`family`、`version` 与 `manifest` 路径（不被清单内容覆盖，清单 `version` 作为 `expected_version` 另列；身份读取抛异常时亦带 `route` 且未读值记为空）。
- **擦除白名单与槽位受保护分区（J4/J5/J6）**：实际擦除集取 `plan.erases` 与 `{metadata, misc, sysdumpdb, uboot_log, sd_klog}` 的交集，被跳过项在 `metrics.skipped_erases` 列出；受保护分区判定前剥离 `_a`/`_b` 后缀并将 `spl`、`splloader` 及其带槽位形式纳入拦截；带 `preserve` 的普通条目不写入，但 `UserData` 条目不论是否带 `preserve` 均写入 `userdata`。
- **前置闸门与子进程有界（J7/J8）**：增加电量校验（`battery_min_level=5`、`skip_battery=false`）与 USB 总线 `1782:4d00` 检查（USB 总线目录缺失或不可列时拒绝；最后一次下载口检查位于缓存准备完成之后、`spd_dump` 启动之前）；`spd_dump_timeout_seconds`（缺省 `3600s`，不超过模板 `7200s` 步骤超时）须为有限正数，总上限覆盖从启动 `spd_dump` 到取得退出码的整个阶段（含发送 `adb reboot autodloader`），发送重启前 `spd_dump` 必须仍在运行（已退出则不发重启并拒绝），超时或 `adb reboot autodloader` 失败时终止并回收子进程且返回失败。
- **成功判据、缓存与干跑（J9/J10/J12）**：成功必须同时满足 `spd_dump` 返回 `0`、正向重启证据（仅限 `boot_id` 前后不同，或 `/proc/uptime` 推算开机时刻晚于发出 `adb reboot autodloader` 的主机时刻，两次读取均须成功；挥发属性仅作否决而不单独构成正向证据）以及无关闭开关的 `manifest.version_prop` 回读等于 `manifest.version`；单次运行只读取一次源 `plan.json` 并在主机锁内共用同一解析结果完成缓存准备（SHA-256 校验与暂存 `.tmp.*` 清理）与命令组装；`dry_run` 经过 J1–J7 校验并优先于 `skip_if_current`。
- **保持不变（J13）**：主机锁路径与语义、`PROGRESS` 戳、CLI 参数与 `spd_dump` 参数形状（含 `keep_charge` 与末尾 `reset`）不变。

涉及文件：`backend/agent/scripts/flash_firmware_unisoc_bsl/flash_firmware_unisoc_bsl.py`、`backend/agent/tests/test_flash_firmware_unisoc_bsl.py`、`backend/schemas/pipeline_templates/flash_unisoc_bsl.json`、`tool_manifest.json`、本文档。

## Alternatives

- **继续在 `plan.json` 缺键时回落 P532AA 默认常量**：已否决，缺键固件包会静默套用错误 FDL 加载地址与分区表，直接违反 #4006 F1/J1。
- **仅靠文件长度与 mtime 判定缓存一致**：已否决，等长替换镜像或篡改 `plan.json` 时仍会命中旧缓存（#4006 F5/J10），改用 SHA-256 校验与源目录直接读 `plan.json`。
- **在 B9-U2 内一并迁移 `spd_dump` 到 `requires_tools`**：已否决，按 #4006 §3 归属 U4（#4010）在 U2 合入且工具包登记完成后实施，避免与 U2 安全闸门耦合。

## Verification

- 第 1 轮（提交 `546816e22`，#4006 v1.0）：运行 5 条通用门禁及第 5/8/14 条变异自证。
- 第 2 轮（依据 #4006 v1.1）：
  - `./scripts/project_python.sh tools/dev/check_script_packages.py`
  - `./scripts/project_python.sh tools/dev/check_tool_manifest.py --base origin/main`
  - `./scripts/project_python.sh scripts/run_gates.py check:quick`
  - `env -i PATH="$PATH" PYTHONPATH=. .venv/bin/python scripts/run_pytest.py backend/agent/tests/ -q`
  - `./scripts/project_python.sh scripts/run_pytest.py tests/ -q --ignore=tests/test_alembic_upgrade.py --ignore=tests/test_script_seed_governance.py --ignore=tests/test_diag_readonly_role_pg.py --ignore=tests/test_seed_default_params_face_3289.py --ignore=tests/test_disarm_guard_migration_3752.py`
  - 1.0.2 基线红绿核对：对第 23–35 条用例在基线 `origin/main` 的 `1.0.2` 脚本上实跑，第 25、31(b)、31(d) 条为绿，其余第 23–35 条均为红。
  - 变异自证（#4006 §4 第 22 条 v1.1）：分别临时变异第 5、7、8、14、20（槽位改写 `boot_a→boot_b`，数量不变）、31 条对应检查并运行对应单测确认变红，随后恢复。

## Revisit

- #4006 §2 的三条模型级问题仍未解决，后续需按对应载体 issue 跟进：
  1. **同 model 不同 product 的身份歧义**（#3762）：A57 与 A57Pro 的 `ro.product.model` 与 `ro.product.device` 相同、仅 `ro.product.name` 不同；A57 系列接入 BSL 前必须先完成 #3762 裁决。
  2. **BSL 口级绑定**（#3995 §5 第 3 项）：`spd_dump` 不支持按串号或 USB 端口选设备，J7 仅在启动前检查总线，无法排除启动后并发设备进入下载模式。
  3. **三份主机锁实现与跨族共享 helper**（#3498）：本批未合并三份主机锁实现。
- 激活与真机核对（`manifest.version` 与 `version_prop` 实机回读值、双机 `dry_run` 与 L2 金丝雀）在 #4006 §5.3 激活 A 中由 Owner 执行；未完成前不得解除 BSL 冻结。
