# 展锐固件制备工具写全 FDL 地址、按类型保留 NV 并支持指定 version_prop（B9-U1）

Status: implemented
Class: bug-fix

载体 issue：#4007（批次 B9 规划方案 #4006 单元 U1）。

## Decision

依据批次 B9 方案（#4006 §3.1、§3.2 K1–K4），收口 [`tools/dev/prepare_unisoc_firmware.py`](../../../tools/dev/prepare_unisoc_firmware.py) 的产出契约与保护判定（涉及文件：[`tools/dev/prepare_unisoc_firmware.py`](../../../tools/dev/prepare_unisoc_firmware.py)、[`tests/test_prepare_unisoc_firmware.py`](../../../tests/test_prepare_unisoc_firmware.py)）：

- **K1（`plan.json` 必备键与失败原子性）**：`build_plan` 始终输出 `erases` 数组（即使为空列表 `[]`）；新增 `validate_plan`，在 `main()` 写出 `images/plan.json` 前强制校验 `entries`（非空列表）、`fdl.fdl1.file` / `fdl.fdl1.base`、`fdl.fdl2.file` / `fdl.fdl2.base`（非空字符串）与 `erases`（列表）。任一缺失即 `raise SystemExit(...)` 非零退出，且不写出缺键的 `plan.json`（并用临时文件 `.part` + `os.replace` 落盘）。
- **K2（按方案 `Type` 保留 NV）**：新增 `PRESERVE_TYPES = {"NV_COMM", "NV_COMM_SLOT"}`，`build_plan` 在既有 `fid in PRESERVE_IDS` 白名单之上同时按 `ftype in PRESERVE_TYPES` 标记 `preserve = True`，避免厂商更换 NV 条目 ID 时静默漏保。现有 `PRESERVE_IDS`（含 `ProdNV`、`Persist`、`Modem_*_DELTANV`、`NV_CALI_*`、`UserData`）保留不变，P532AA 与 P525AA 现有产物集不变。
- **K3（`erases` 如实记录）**：保持 `EraseFlash2` 条目原样输出，不在制备工具侧过滤 `splloader` 等分区，把擦除白名单过滤唯一留在消费端脚本（#4006 J4）。
- **K4（可选 `--version-prop`）**：为 CLI 新增 `--version-prop` 可选参数（缺省仍为 `ro.build.sw_internal_version`，拒绝空白值），写入 `manifest.json` 的 `version_prop` 键，供真机属性核对不一致时按需重新生成清单（#4006 §5.3 第 1 步）。

## Alternatives

- **在 `build_plan()` 内部直接强制抛出 FDL 缺失错误**：`build_plan` 承担纯字典构建，既有单元测试 `test_build_plan_marks_device_unique_entries_preserve` 只构造 NV/BOOT 条目调用 `build_plan`；将契约校验放在 `validate_plan` 并由 `main()` 在写盘前调用，既保证 CLI 严格满足 K1 且不写出缺键 `plan.json`，又保持既有测试夹具兼容。
- **仅靠 `PRESERVE_IDS` 继续追加 ID**：无法防止新固件方案更改 NV 条目 `ID`，违背 K2 不依赖已知清单的要求；完全改用 `PRESERVE_TYPES` 替代 `PRESERVE_IDS` 则会漏掉 `Type=CODE2` 的 `Modem_NR_DELTANV`、`ProdNV` 等，故两者取并集。

## Verification

- 修正前对新增测试执行 `./scripts/project_python.sh scripts/run_pytest.py tests/test_prepare_unisoc_firmware.py -v`：既有 4 项与 K3 回归用例（`splloader` 保留在 `erases`）通过，K1（缺 `fdl2` base / 缺 `fdl1`）、K2（未知 ID + `NV_COMM_SLOT`）、`erases` 空列表建键与 K4（`--version-prop`）均为红（5 failed, 5 passed）。
- 修正后：
  - `./scripts/project_python.sh scripts/run_pytest.py tests/test_prepare_unisoc_firmware.py -v` → `10 passed in 0.06s`
  - `./scripts/project_python.sh tools/dev/check_script_packages.py` → `[OK] check_script_packages：40 个族树与 tool_manifest.json 最新登记等价`
  - `./scripts/project_python.sh tools/dev/check_tool_manifest.py --base origin/main` → `OK: tool_manifest 绿（46 族 / 279 版本条目；相对 origin/main append-only）`
  - `./scripts/project_python.sh scripts/run_gates.py check:quick` → `[OK] check:quick (16 gates)`
  - `env -i PATH="$PATH" PYTHONPATH=. .venv/bin/python scripts/run_pytest.py backend/agent/tests/ -q` → `1 failed, 3424 passed, 2 skipped`（唯一失败为与本改动无关的宿主机环境耦合用例 `backend/agent/tests/test_flash_preflight_v102.py::test_dialout_absent_and_no_rule_fails`：本机 `/etc/udev/rules.d` 存在 `MODE="0666"` 规则导致 `_udev_rule_form` 返回 `"0666"`）
  - `./scripts/project_python.sh scripts/run_pytest.py tests/ -q --ignore=tests/test_alembic_upgrade.py --ignore=tests/test_script_seed_governance.py --ignore=tests/test_diag_readonly_role_pg.py --ignore=tests/test_seed_default_params_face_3289.py --ignore=tests/test_disarm_guard_migration_3752.py` → `2527 passed, 18 skipped`

## Revisit

- 批次 B9 激活阶段（#4006 §5.3 第 1–2 步）需使用本工具 `--no-extract` 为 P532AA 重新生成 `plan.json`；若真机 `ro.build.sw_internal_version` 与 `manifest.version` 不一致，使用 `--version-prop` 重新生成 `manifest.json`。
